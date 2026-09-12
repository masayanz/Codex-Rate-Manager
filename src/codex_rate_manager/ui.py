from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import time

from PySide6.QtCore import Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QScrollArea, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QSystemTrayIcon, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QHeaderView, QSizePolicy,
)

from .storage import Config, accessible_config
from .meters import RingMeter, SegmentBar, tray_icon
from .resources import resource_path
from .skin_manager import SkinManager, default_stylesheet
from .screenshot_service import ScreenshotController

STYLE = default_stylesheet()

LABELS = {"CONNECTING": "Codexへ接続しています...", "AVAILABLE": "Codex 使用可能", "LOW": "Codex 使用可能", "LIMITED_5H": "Codex 5時間レート上限", "LIMITED_WEEKLY": "Codex 週間レート上限", "LIMITED_OTHER": "Codex その他のレート上限", "WAITING_RESET": "リセットの確認待ち", "VERIFYING": "利用可能か確認しています...", "DISCONNECTED": "Codex情報を取得できません", "ERROR": "レート情報を確認できません"}


def local_date(epoch):
    try:
        return datetime.fromtimestamp(epoch).strftime("%Y/%m/%d %H:%M:%S") if epoch else "未取得"
    except (ValueError, OSError, OverflowError):
        return "日時を解釈できません"


def countdown(epoch):
    if not epoch:
        return "あと —"
    seconds = max(0, int(epoch - time.time()))
    if not seconds:
        return "予定時刻を経過・Codexの確認待ち"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"あと {f'{days}日 ' if days else ''}{hours:02}:{minutes:02}:{seconds:02}"


class RateCard(QFrame):
    def __init__(self, title, subtitle, skins):
        super().__init__()
        self.setObjectName("card")
        self.skins = skins
        self.window = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)
        top = QHBoxLayout()
        top.addWidget(QLabel(title))
        label = QLabel(subtitle)
        label.setObjectName("eyebrow")
        top.addStretch()
        top.addWidget(label)
        layout.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(20)
        self.gauge = RingMeter(skins)
        body.addWidget(self.gauge)
        details = QVBoxLayout()
        details.setSpacing(5)
        self.remaining = QLabel("残り — %")
        self.remaining.hide()
        self.used = QLabel("使用率：未取得")
        details.addWidget(self.used)
        self.bar = SegmentBar(skins)
        details.addWidget(self.bar)
        self.reset = QLabel("次回リセット：未取得")
        self.reset.setWordWrap(True)
        self.reset.setObjectName("muted")
        details.addWidget(self.reset)
        self.count = QLabel("あと —")
        self.count.setWordWrap(True)
        details.addWidget(self.count)
        body.addLayout(details, 1)
        layout.addLayout(body)
        self.stale = QLabel("最後に取得した値・再確認中")
        self.stale.setObjectName("muted")
        self.stale.hide()
        layout.addWidget(self.stale)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def update_rate(self, window, stale=False):
        self.window = window
        self.remaining.setText(f"残り {window.remaining:g}%" if window else "残り — %")
        value = window.remaining if window and not stale else None
        self.gauge.set_value(value)
        self.bar.set_value(value)
        self.used.setText(f"使用率：{window.used_percent:g}%" if window else "使用率：未取得")
        self.stale.setVisible(bool(stale and window))
        self.reset.setText("次回リセット：\n" + local_date(window.reset_at if window else None))
        self.tick()

    def tick(self):
        self.count.setText(countdown(self.window.reset_at if self.window else None))


class MainWindow(QMainWindow):
    refresh = Signal()
    settings_requested = Signal()
    history_requested = Signal()
    diagnostics_requested = Signal()
    quit_requested = Signal()
    close_without_tray = Signal()

    def __init__(self, mock=False, skins=None):
        super().__init__()
        self.skins = skins or SkinManager()
        self.position_manager = None
        self.state = "CONNECTING"
        self.setWindowTitle("Codex Rate Manager" + (" — モック" if mock else ""))
        self.resize(520, 720)
        self.setMaximumWidth(560)
        self.tray_enabled = True
        self.exiting = False
        self.setWindowIcon(QIcon(str(resource_path("assets/app.ico"))))
        central = QWidget()
        self.setCentralWidget(central)
        self.outer = QVBoxLayout(central)
        self.outer.setSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.scroll.setWidget(content)
        self.outer.addWidget(self.scroll, 1)
        self.content_layout = layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        top = QHBoxLayout()
        brand = QLabel("CODEX RATE MANAGER\nMONITOR • SYNC • NOTIFY")
        brand.setObjectName("eyebrow")
        top.addWidget(brand)
        top.addStretch()
        small = QLabel("MOCK" if mock else "v0.1\nRATE MONITOR")
        small.setObjectName("eyebrow")
        top.addWidget(small)
        layout.addLayout(top)
        status_panel = QFrame()
        status_panel.setObjectName("card")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 9, 14, 9)
        status_layout.setSpacing(3)
        self.status = QLabel(LABELS["CONNECTING"])
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        status_layout.addWidget(self.status)
        self.description = QLabel("両方のレート枠を確認して、復帰をお知らせします。")
        self.description.setObjectName("muted")
        self.description.setWordWrap(True)
        status_layout.addWidget(self.description)
        layout.addWidget(status_panel)
        self.five = RateCard("5時間レート", "5H WINDOW", self.skins)
        self.weekly = RateCard("週間レート", "WEEKLY WINDOW", self.skins)
        layout.addWidget(self.five)
        layout.addWidget(self.weekly)
        connection_panel = QFrame()
        connection_panel.setObjectName("card")
        connection_layout = QVBoxLayout(connection_panel)
        connection_layout.setContentsMargins(14, 8, 14, 8)
        connection_layout.setSpacing(3)
        self.connection = QLabel("Codex接続：接続待ち")
        self.connection.setWordWrap(True)
        self.connection.setObjectName("muted")
        connection_layout.addWidget(self.connection)
        self.updated = QLabel("最終更新：—")
        self.updated.setObjectName("muted")
        connection_layout.addWidget(self.updated)
        layout.addWidget(connection_panel)
        self.toolbar = QWidget()
        buttons = QHBoxLayout(self.toolbar)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        for text, signal in (("今すぐ更新", self.refresh), ("履歴", self.history_requested), ("設定", self.settings_requested), ("診断", self.diagnostics_requested)):
            button = QPushButton(text)
            if text == "今すぐ更新":
                button.setObjectName("primary")
            button.clicked.connect(signal.emit)
            buttons.addWidget(button)
        self.outer.addWidget(self.toolbar)
        self.screenshot_message = QLabel("")
        self.screenshot_message.setObjectName("muted")
        self.screenshot_message.setWordWrap(True)
        self.outer.addWidget(self.screenshot_message)
        self.screenshot = ScreenshotController(self)
        self.screenshot_button = QPushButton("スクショ")
        self.screenshot_button.setToolTip("画面をコピー (Ctrl+Shift+C)")
        self.screenshot_button.clicked.connect(self.screenshot.request)
        buttons.addWidget(self.screenshot_button)
        self.screenshot_shortcut = QShortcut(QKeySequence("Ctrl+Shift+C"), self)
        self.screenshot_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.screenshot_shortcut.activated.connect(self.screenshot.request)
        self.skins.skin_changed.connect(self.apply_skin)
        self.apply_skin()

    def apply_skin(self, *args):
        tokens = self.skins.tokens
        margin = tokens.layout("outer_margin", 18)
        self.outer.setContentsMargins(margin, margin, margin, margin)
        self.content_layout.setSpacing(tokens.layout("card_spacing", 10))
        self.status.setStyleSheet(f"color: {tokens.state_color(self.state)};")
        self.update()

    def update_status(self, snapshot, state, detail):
        self.status.setText("● " + LABELS.get(state, state))
        self.state = state
        self.apply_skin()
        self.description.setText("レート残量が少なくなっています。" if state == "LOW" else "接続前の値は利用可能判定に使いません。" if state == "DISCONNECTED" else "両方のレート枠を確認して、復帰をお知らせします。")
        stale = state in ("DISCONNECTED", "CONNECTING", "ERROR", "VERIFYING")
        self.five.update_rate(snapshot.five_hour if snapshot else None, stale)
        self.weekly.update_rate(snapshot.weekly if snapshot else None, stale)
        self.connection.setText(("● ONLINE   " if not stale else "● OFFLINE   ") + "Codex接続：" + detail)
        self.updated.setText(("最終成功：" if stale else "最終更新：") + (local_date(snapshot.fetched_at) if snapshot else "—"))

    def show_front(self):
        if self.position_manager:
            self.position_manager.ensure_visible()
        self.showNormal()
        if self.position_manager:
            self.position_manager.ensure_visible()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.exiting:
            event.accept()
        elif self.tray_enabled and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
        else:
            event.ignore()
            self.close_without_tray.emit()


class SettingsDialog(QDialog):
    discord_enabled_changed = Signal(bool)
    save_requested = Signal(object, object)
    test_requested = Signal(object)
    reconnect = Signal()
    appearance_changed = Signal(str, bool, bool)
    tray_visibility_changed = Signal(bool)

    def __init__(self, config, has_webhook, parent, mock=False, skins=None):
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.resize(570, 480)
        self.config = config
        self.fields = {}
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        def tab(name):
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(18, 18, 18, 18)
            form.setSpacing(14)
            tabs.addTab(page, name)
            return form

        def check(form, key, text):
            box = QCheckBox(text)
            box.setChecked(getattr(config, key))
            self.fields[key] = box
            form.addRow(box)
            return box

        general = tab("一般")
        auto = check(general, "autostart", "Windowsログイン時に起動")
        auto.setEnabled(not mock)
        check(general, "show_on_start", "起動時にウィンドウを表示")
        general.addRow(QLabel("タスクトレイ"))
        check(general, "tray_enabled", "タスクトレイアイコンを表示する").toggled.connect(self.tray_visibility_changed.emit)
        hint = QLabel("表示切替は即時反映・保存されます。\nWindows側の表示位置はWindowsの設定で変更できます。")
        hint.setWordWrap(True)
        general.addRow(hint)
        self.taskbar_settings = QPushButton("Windowsの通知領域設定を開く")
        self.taskbar_settings.clicked.connect(self.open_taskbar_settings)
        general.addRow(self.taskbar_settings)
        monitor = tab("監視")
        for key, label, low, high, suffix in (("normal_interval", "通常監視間隔", 30, 3600, " 秒"), ("near_interval", "リセット10分前の監視間隔", 30, 300, " 秒"), ("low_threshold", "低残量警告", 1, 99, " %")):
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setSuffix(suffix)
            spin.setValue(getattr(config, key))
            self.fields[key] = spin
            monitor.addRow(label, spin)
        monitor.addRow(QLabel("1分前は30秒間隔、予定時刻に再取得します。"))
        notifications = tab("通知")
        check(notifications, "notifications_enabled", "通知を有効にする")
        for kind, title in (("low", "残量低下"), ("limit", "上限到達"), ("reset", "各レート枠の復帰")):
            check(notifications, "windows_" + kind, "Windows：" + title)
        self.reminders = {}
        row = QHBoxLayout()
        for minute in (30, 10, 5, 1):
            box = QCheckBox(f"{minute}分前")
            box.setChecked(minute in config.reminders)
            self.reminders[minute] = box
            row.addWidget(box)
        notifications.addRow("事前通知（両チャネル）", row)
        discord = tab("Discord")
        check(discord, "discord_enabled", "Discord通知を有効にする（即時保存）").toggled.connect(self.discord_enabled_changed.emit)
        self.webhook = QLineEdit()
        self.webhook.setEchoMode(QLineEdit.EchoMode.Password)
        self.webhook.setPlaceholderText("登録済み（変更する場合のみ入力）" if has_webhook else "https://discord.com/api/webhooks/…")
        discord.addRow("Webhook URL", self.webhook)
        self.clear_webhook = QCheckBox("登録済みWebhookを削除")
        discord.addRow(self.clear_webhook)
        for kind, title in (("low", "残量低下"), ("limit", "上限到達"), ("reset", "各レート枠の復帰")):
            check(discord, "discord_" + kind, title)
        self.discord_status = QLabel()
        self.discord_status.setWordWrap(True)
        discord.addRow(self.discord_status)
        for key in ("notifications_enabled", "discord_enabled", "discord_reset"):
            self.fields[key].toggled.connect(self.update_discord_status)
        self.update_discord_status()
        self.test_button = QPushButton("Discord通知テスト")
        self.test_button.clicked.connect(self.test)
        discord.addRow(self.test_button)
        codex = tab("Codex")
        self.codex_path = QLineEdit(config.codex_path)
        self.codex_path.setPlaceholderText("自動検出")
        codex.addRow("Codex executable", self.codex_path)
        browse = QPushButton("ファイルを選択")
        browse.clicked.connect(self.browse)
        codex.addRow(browse)
        reconnect = QPushButton("接続再確認（保存済みの設定で実行）")
        reconnect.clicked.connect(self.reconnect.emit)
        codex.addRow(reconnect)
        codex.addRow(QLabel("バージョン・接続状態はメイン画面の「診断」で確認できます。"))
        appearance = tab("外観")
        self.skins = skins or parent.skins
        self.skin_select = QComboBox()
        for skin in self.skins.available_skins():
            self.skin_select.addItem(skin["name"], skin["id"])
        self.skin_select.setCurrentIndex(max(0, self.skin_select.findData(config.skin_id)))
        appearance.addRow("スキン", self.skin_select)
        check(appearance, "glow_enabled", "発光エフェクト")
        check(appearance, "animation_enabled", "メーターアニメーション")
        appearance.addRow(QLabel("選択直後に本体へプレビューします。保存で確定します。"))
        self.skin_select.currentIndexChanged.connect(self.preview)
        self.fields["glow_enabled"].toggled.connect(self.preview)
        self.fields["animation_enabled"].toggled.connect(self.preview)
        self.tray_style = QComboBox()
        self.tray_style.addItem("二重リング（外周5時間・内周週間）", "rings")
        self.tray_style.addItem("上下バー（上5時間・下週間）", "bars")
        self.tray_style.setCurrentIndex(max(0, self.tray_style.findData(config.tray_style)))
        appearance.addRow("タスクトレイ表示", self.tray_style)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("閉じる")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def open_taskbar_settings(self):
        # Microsoft documents this URI for the Windows taskbar settings page.
        if not QDesktopServices.openUrl(QUrl("ms-settings:taskbar")):
            self.feedback.setText("Windows設定を開けませんでした。タスクバーを右クリックし「タスクバーの設定」を開いてください。")
        else:
            self.feedback.setText("Windowsのタスクバー設定で通知領域の表示位置を変更できます。")

    def update_discord_status(self):
        enabled = all(self.fields[key].isChecked() for key in ("notifications_enabled", "discord_enabled", "discord_reset"))
        self.discord_status.setText(
            "復帰通知：ON（変更後は保存してください）。テストは接続のみを確認します。"
            if enabled else
            "復帰通知：OFF。通知全体・Discord通知・各レート枠の復帰をONにして保存してください。テスト成功だけでは実通知は有効になりません。"
        )

    def sync_tray(self, visible, pending=False):
        box = self.fields["tray_enabled"]
        previous = box.blockSignals(True)
        box.setChecked(visible)
        box.blockSignals(previous)
        box.setEnabled(not pending)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not pending)

    def preview(self, *args):
        self.appearance_changed.emit(self.skin_select.currentData(),
                                    self.fields["glow_enabled"].isChecked(),
                                    self.fields["animation_enabled"].isChecked())

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Codex CLIを選択", "", "Codex (*.exe *.cmd)")
        if path:
            self.codex_path.setText(path)

    def value_webhook(self):
        return "" if self.clear_webhook.isChecked() else (self.webhook.text().strip() or None)

    def valid(self):
        from .notifications import validate_webhook
        value = self.value_webhook()
        if value and not validate_webhook(value):
            self.feedback.setText("Webhook URLの形式を確認してください。DiscordのWebhook URLを入力してください。")
            return False
        return True

    def test(self):
        if self.valid():
            self.test_button.setEnabled(False)
            self.feedback.setText("Discordへテスト通知を送信しています...")
            self.test_requested.emit(self.value_webhook())

    def save(self):
        if not self.valid():
            return
        values = {key: widget.isChecked() if isinstance(widget, QCheckBox) else widget.value() for key, widget in self.fields.items()}
        values["skin_id"] = self.skin_select.currentData()
        values["tray_style"] = self.tray_style.currentData()
        values["reminders"] = [minute for minute, box in self.reminders.items() if box.isChecked()]
        values["codex_path"] = self.codex_path.text().strip()
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.feedback.setText("設定を保存しています...")
        config = accessible_config(replace(self.config, **values))
        self.sync_tray(config.tray_enabled, True)
        self.fields["show_on_start"].setChecked(config.show_on_start)
        self.save_requested.emit(config, self.value_webhook())

    def result(self, kind, success, message):
        self.feedback.setText(message)
        self.test_button.setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        self.fields["tray_enabled"].setEnabled(True)


class HistoryDialog(QDialog):
    refresh = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("履歴")
        self.resize(860, 480)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tables = {}
        for key, title in (("rates", "レート履歴"), ("notifications", "通知履歴"), ("events", "イベント")):
            table = QTableWidget()
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setWordWrap(False)
            table.horizontalHeader().setStretchLastSection(True)
            self.tables[key] = table
            self.tabs.addTab(table, title)
        layout.addWidget(self.tabs)
        hint = QLabel("最新300件を表示します。")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        button = QPushButton("更新")
        button.clicked.connect(self.reload)
        layout.addWidget(button)
        self.tabs.currentChanged.connect(self.reload)

    def reload(self):
        self.refresh.emit(list(self.tables)[self.tabs.currentIndex()])

    def fill(self, kind, data):
        headers, rows = data
        if kind == "rates":
            headers = ["日時", "5時間残量", "週間残量", "状態"]
            rows = [(r[0], r[2], r[5], LABELS.get(r[7], r[7])) for r in rows]
        elif kind == "notifications":
            kinds = {"low": "残量低下", "limit": "上限到達", "reset": "各レート枠の復帰", "reminder": "事前通知", "test": "テスト"}
            rows = [(r[0], kinds.get(r[1], r[1]), r[2], r[3], r[4]) for r in rows]
        table = self.tables[kind]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                table.setItem(i, j, QTableWidgetItem(str(value) if value is not None else "—"))
        table.resizeColumnsToContents()


class DiagnosticsDialog(QDialog):
    mock_changed = Signal(str)

    def __init__(self, parent, mock=False):
        super().__init__(parent)
        self.setWindowTitle("診断情報")
        self.resize(740, 540)
        layout = QVBoxLayout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)
        self.raw = QCheckBox("最新レスポンスを表示（秘密情報はマスク）")
        self.raw.toggled.connect(lambda: self.update_data(self.data))
        layout.addWidget(self.raw)
        self.data = {}
        if mock:
            row = QHBoxLayout()
            for state, title in (("AVAILABLE", "使用可能"), ("LOW", "低残量"), ("LIMITED_5H", "5h上限"), ("LIMITED_WEEKLY", "週間上限"), ("RESET", "復帰"), ("DISCONNECTED", "切断")):
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False, value=state: self.mock_changed.emit(value))
                row.addWidget(button)
            layout.addLayout(row)

    def update_data(self, data):
        self.data = data
        self.text.setPlainText("\n\n".join(f"{key}\n{value}" for key, value in data.items() if self.raw.isChecked() or not key.startswith("最新レスポンス")))
