from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .monitor import Monitor
from .storage import Config, accessible_config
from dataclasses import replace
from .meters import tray_icon
from .skin_manager import SkinManager
from .resources import resource_path
from .window_position import WindowPositionManager
from .ui import LABELS, local_date
from .ui import MainWindow, SettingsDialog, HistoryDialog, DiagnosticsDialog


class ResumeFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if os.name == "nt" and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            from ctypes.wintypes import MSG
            msg = MSG.from_address(int(message))
            if msg.message == 0x218 and msg.wParam in (7, 18):
                self.callback()
        return False, 0


class Application:
    def __init__(self, app, args, data_dir, server):
        self.app, self.args, self.data_dir = app, args, data_dir
        self.server = server
        self.config = Config()
        self.has_webhook = False
        self.config_loaded = False
        self.diagnostics_data = {}
        self.latest_state = "CONNECTING"
        self.latest_snapshot = None
        self.settings = self.history = self.diagnostics = None
        self.quitting = False
        self.tray_pending = False
        self.hide_after_tray_save = False
        self.close_dialog = None
        self.first_config = True
        self.skins = SkinManager(data_dir)
        self.app_icon = QIcon(str(resource_path("assets/app.ico")))
        self.app.setWindowIcon(self.app_icon)
        self.window = MainWindow(args.mock, self.skins)
        self.positions = WindowPositionManager(self.window)
        self.window.position_manager = self.positions
        self.window.show()
        self.tray = QSystemTrayIcon(self.app_icon, app)
        self.tray.setToolTip("Codex Rate Manager\nCodexへ接続しています...")
        menu = QMenu()
        menu.addAction("Codex Rate Managerを開く", self.window.show_front)
        self.tray_five = menu.addAction("5時間レート：未取得")
        self.tray_weekly = menu.addAction("週間レート：未取得")
        self.tray_five.setEnabled(False)
        self.tray_weekly.setEnabled(False)
        menu.addSeparator()
        menu.addAction("履歴", self.open_history)
        menu.addAction("診断", self.open_diagnostics)
        menu.addAction("今すぐ更新", lambda: self.monitor.command("refresh"))
        menu.addAction("Codexを起動", lambda: self.monitor.command("launch"))
        menu.addAction("Discord通知テスト", lambda: self.monitor.command("test"))
        self.toggle = menu.addAction("通知 ON/OFF")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.triggered.connect(lambda checked: self.monitor.command("toggle", checked))
        menu.addAction("設定", self.open_settings)
        menu.addSeparator()
        menu.addAction("終了", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.window.show_front() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        # Wait for persisted visibility before registering the tray icon.
        # Until then, closing the window must use the safety dialog.
        self.window.tray_enabled = False
        self.monitor = Monitor(data_dir, args.mock)
        self.positions.changed.connect(self.save_position)
        self.skins.skin_changed.connect(self.update_tray)
        self.monitor.signals.update.connect(self.update)
        self.monitor.signals.config.connect(self.on_config)
        self.monitor.signals.diagnostics.connect(self.on_diagnostics)
        self.monitor.signals.history.connect(self.on_history)
        self.monitor.signals.result.connect(self.on_result)
        self.monitor.signals.stopped.connect(self.finished)
        self.window.refresh.connect(lambda: self.monitor.command("refresh"))
        self.window.settings_requested.connect(self.open_settings)
        self.window.history_requested.connect(self.open_history)
        self.window.diagnostics_requested.connect(self.open_diagnostics)
        self.window.quit_requested.connect(self.quit)
        self.window.close_without_tray.connect(self.confirm_close_without_tray)
        self.server.newConnection.connect(self.activate_existing)
        self.last_tick = time.time()
        self.timer = QTimer(app)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.resume_filter = ResumeFilter(lambda: self.monitor.command("resume"))
        app.installNativeEventFilter(self.resume_filter)
        # GUI・トレイが描画された後にDBとCodexを初期化する。
        QTimer.singleShot(0, self.monitor.start)
        if getattr(args, "verify_tray", False):
            from .tray_verification import start_tray_verification
            start_tray_verification(self)
        elif args.smoke_test:
            QTimer.singleShot(args.smoke_test * 1000, self.smoke_finish)

    def activate_existing(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
        self.window.show_front()

    def tick(self):
        now = time.time()
        if now - self.last_tick > 10 or now < self.last_tick:
            self.monitor.command("resume")
        self.last_tick = now
        self.window.five.tick()
        self.window.weekly.tick()

    def update(self, snapshot, state, detail):
        self.latest_state = state
        self.latest_snapshot = snapshot
        self.window.update_status(snapshot, state, detail)
        self.update_tray()

    def update_tray(self, *args):
        snapshot, state = self.latest_snapshot, self.latest_state
        five = snapshot.five_hour if snapshot else None
        week = snapshot.weekly if snapshot else None
        try:
            icon = tray_icon(five.remaining if five else None, week.remaining if week else None, state, self.config.tray_style, self.skins.tokens)
            self.tray.setIcon(icon if not icon.isNull() else self.app_icon)
        except Exception:
            self.tray.setIcon(self.app_icon)
        five_text = f"{five.remaining:g}%" if five else "未取得"
        week_text = f"{week.remaining:g}%" if week else "未取得"
        stale = state in {"CONNECTING", "DISCONNECTED", "VERIFYING", "ERROR", "WAITING_RESET"}
        suffix = "（最終取得値）" if stale and snapshot else ""
        self.tray_five.setText(f"5時間レート：{five_text}{suffix}")
        self.tray_weekly.setText(f"週間レート：{week_text}{suffix}")
        self.tray.setToolTip(
            f"Codex Rate Manager\n5時間: {five_text} / 週間: {week_text}{suffix}\n"
            f"5時間リセット: {local_date(five.reset_at) if five else '—'}\n"
            f"週間リセット: {local_date(week.reset_at) if week else '—'}\n状態: {LABELS.get(state, state)}"
        )

    def save_position(self, value):
        if self.config_loaded and not self.quitting:
            self.monitor.command("window", value)

    def apply_appearance(self, *args):
        self.skins.apply(self.config.skin_id, self.config.glow_enabled, self.config.animation_enabled)
        self.positions.ensure_visible()

    def set_tray_visible(self, visible):
        # Restore the window before removing its only other entry point.
        if not visible and not self.window.isVisible():
            self.window.show_front()
        self.window.tray_enabled = visible
        if visible:
            self.tray.show()
        else:
            self.tray.hide()

    def request_tray_visibility(self, visible, hide_after=False):
        if self.tray_pending or not self.config_loaded:
            return
        self.tray_pending = True
        self.hide_after_tray_save = hide_after
        if not hide_after:
            effective = accessible_config(replace(self.config, tray_enabled=visible)).tray_enabled
            self.set_tray_visible(effective)
        if self.settings:
            self.settings.sync_tray(self.window.tray_enabled, True)
            self.settings.feedback.setText("トレイ表示設定を保存しています...")
        self.monitor.command("tray_visibility", visible)

    def confirm_close_without_tray(self):
        if self.close_dialog and self.close_dialog.isVisible():
            self.close_dialog.raise_()
            return
        dialog = QMessageBox(self.window)
        dialog.setWindowTitle("ウィンドウを閉じる")
        dialog.setText("タスクトレイアイコンが非表示、または通知領域が利用できません。\nこのままウィンドウを閉じると、アプリを操作できなくなります。")
        exit_button = dialog.addButton("アプリを終了", QMessageBox.ButtonRole.DestructiveRole)
        tray_button = dialog.addButton("タスクトレイを表示して閉じる", QMessageBox.ButtonRole.ActionRole)
        cancel = dialog.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        tray_button.setEnabled(self.config_loaded and not self.tray_pending and QSystemTrayIcon.isSystemTrayAvailable())
        def selected(button):
            if button == exit_button:
                self.quit()
            elif button == tray_button:
                self.request_tray_visibility(True, hide_after=True)
        dialog.buttonClicked.connect(selected)
        self.close_dialog = dialog
        dialog.open()

    def on_config(self, config, has_webhook):
        self.config, self.has_webhook = config, has_webhook
        self.config_loaded = True
        self.apply_appearance()
        if self.first_config:
            self.positions.restore(config.window)
        self.set_tray_visible(config.tray_enabled)
        self.toggle.setChecked(config.notifications_enabled)
        self.update_tray()
        if self.first_config and not config.show_on_start and config.tray_enabled and QSystemTrayIcon.isSystemTrayAvailable():
            self.window.hide()
        self.first_config = False
        if self.settings:
            self.settings.config = config
            self.settings.sync_tray(config.tray_enabled, self.tray_pending)

    def on_diagnostics(self, data):
        self.diagnostics_data = data
        if self.diagnostics:
            self.diagnostics.update_data(data)

    def on_history(self, kind, data):
        if self.history:
            self.history.fill(kind, data)

    def on_result(self, kind, success, message):
        if kind == "Discord設定" and self.settings:
            box = self.settings.fields["discord_enabled"]
            blocked = box.blockSignals(True)
            box.setChecked(self.config.discord_enabled)
            box.blockSignals(blocked)
            self.settings.update_discord_status()

        if kind == "トレイ表示":
            hide_after = self.hide_after_tray_save
            self.tray_pending = self.hide_after_tray_save = False
            self.set_tray_visible(self.config.tray_enabled)
            if success and hide_after and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
                self.window.hide()
            elif not success or hide_after:
                self.window.show_front()
            if self.settings:
                self.settings.sync_tray(self.config.tray_enabled)
                self.settings.feedback.setText(message)
            if success:
                return
        if self.settings and self.settings.isVisible():
            self.settings.result(kind, success, message)
        elif not self.args.smoke_test:
            box = QMessageBox(self.window)
            box.setWindowTitle(kind)
            box.setText(message)
            box.setIcon(QMessageBox.Icon.Information if success else QMessageBox.Icon.Warning)
            box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            box.open()
            self.message_box = box

    def open_settings(self):
        if not self.config_loaded:
            self.window.connection.setText("設定を読み込んでいます。しばらくお待ちください。")
            return
        if self.settings and self.settings.isVisible():
            self.settings.raise_()
            return
        self.settings = SettingsDialog(self.config, self.has_webhook, self.window, self.args.mock, self.skins)
        self.settings.tray_visibility_changed.connect(self.request_tray_visibility)
        self.settings.sync_tray(self.config.tray_enabled, self.tray_pending)
        self.settings.appearance_changed.connect(self.skins.apply)
        self.settings.finished.connect(self.apply_appearance)
        self.settings.discord_enabled_changed.connect(lambda enabled: self.monitor.command("discord_enabled", enabled))
        self.settings.save_requested.connect(lambda config, url: self.monitor.command("save", (config, url)))
        self.settings.test_requested.connect(lambda url: self.monitor.command("test", url))
        self.settings.reconnect.connect(lambda: self.monitor.command("reconnect"))
        self.settings.show()

    def open_history(self):
        if not self.history:
            self.history = HistoryDialog(self.window)
            self.history.refresh.connect(lambda kind: self.monitor.command("history", kind))
        self.history.show()
        self.history.raise_()
        self.history.reload()

    def open_diagnostics(self):
        if not self.diagnostics:
            self.diagnostics = DiagnosticsDialog(self.window, self.args.mock)
            self.diagnostics.mock_changed.connect(lambda state: self.monitor.command("mock", state))
        self.diagnostics.update_data(self.diagnostics_data)
        self.diagnostics.show()
        self.diagnostics.raise_()

    def smoke_finish(self):
        # 開発用の実アプリ診断。レート以外の応答やWebhookは書き出さない。
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.window.grab().save(str(self.data_dir / "main-window.png"))
        report = {"state": self.latest_state, "gui_visible": self.window.isVisible(), "tray_available": QSystemTrayIcon.isSystemTrayAvailable(), "diagnostics": self.diagnostics_data}
        (self.data_dir / "smoke-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self.quit()

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.window.connection.setText("監視を停止しています...")
        self.window.setEnabled(False)
        self.positions.ensure_visible()
        self.monitor.command("shutdown", self.positions.capture())
        if not self.monitor.is_alive():
            self.finished()

    def finished(self):
        if not self.quitting:
            return
        self.tray.hide()
        self.window.exiting = True
        self.server.close()
        self.app.quit()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex Rate Manager")
    parser.add_argument("--mock", action="store_true", help="独立データでモック状態を診断画面から切替")
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--data-dir", type=Path, help="開発・検証用の保存先")
    parser.add_argument("--smoke-test", type=int, metavar="SECONDS", help="実アプリを一定時間起動し、診断と画面を保存して終了")
    parser.add_argument("--verify-tray", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--verify-recovery", action="store_true", help="モックのレート復帰・通知経路を検証して終了")
    parser.add_argument("--live-notifications", action="store_true", help="保存済みWebhookへテスト1回と復帰2回を送信（--verify-recovery必須）")
    args = parser.parse_args(argv)
    if args.verify_tray and not args.mock:
        parser.error("--verify-tray は --mock と併用してください。")
    if args.verify_recovery and not args.mock:
        parser.error("--verify-recovery は --mock と併用してください。")
    if args.live_notifications and not args.verify_recovery:
        parser.error("--live-notifications は --verify-recovery と併用してください。")
    if args.verify_recovery:
        if args.data_dir is None:
            parser.error("--verify-recovery は空の --data-dir を指定してください。")
        from .recovery_verification import run_recovery_verification
        report = run_recovery_verification(args.data_dir, live_notifications=args.live_notifications)
        if sys.stdout is not None:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1
    data_dir = args.data_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexRateManager" / ("mock" if args.mock else "")
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CodexRateManager.Desktop")
    app = QApplication(sys.argv[:1])
    app.setWindowIcon(QIcon(str(resource_path("assets/app.ico"))))
    app.setApplicationName("Codex Rate Manager")
    app.setOrganizationName("CodexRateManager")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    # QLocalServerはWindowsのユーザー限定Named Pipe。保存先ごとに一つ。
    identity = hashlib.sha256(str(data_dir.resolve()).casefold().encode()).hexdigest()[:24]
    server_name = "CodexRateManager-" + identity
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(200):
        probe.disconnectFromServer()
        return 0
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(server_name):
        QMessageBox.warning(None, "Codex Rate Manager", "既に起動しているか、二重起動防止用の接続を作成できません。")
        return 1
    controller = Application(app, args, data_dir, server)
    app.aboutToQuit.connect(controller.monitor.stop)
    return app.exec()
