from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QSystemTrayIcon, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QHeaderView,
)

from .storage import Config

COLORS = {"AVAILABLE": "#36bf91", "LOW": "#edbd5c", "LIMITED_5H": "#ef7180", "LIMITED_WEEKLY": "#ef7180", "LIMITED_OTHER": "#ef7180"}
LABELS = {"CONNECTING": "Codexへ接続しています...", "AVAILABLE": "Codex 使用可能", "LOW": "Codex 使用可能", "LIMITED_5H": "Codex 5時間レート上限", "LIMITED_WEEKLY": "Codex 週間レート上限", "LIMITED_OTHER": "Codex その他のレート上限", "WAITING_RESET": "リセットの確認待ち", "VERIFYING": "利用可能か確認しています...", "DISCONNECTED": "Codex情報を取得できません", "ERROR": "レート情報を確認できません"}

STYLE = """
QWidget { background: #101923; color: #e3edf5; font-family: 'Yu Gothic UI'; font-size: 14px; }
QMainWindow, QDialog { background: #101923; }
QLabel#eyebrow { color: #7e9cae; font-size: 12px; font-weight: 600; }
QLabel#status { font-size: 26px; font-weight: 700; }
QLabel#remaining { font-size: 34px; font-weight: 700; }
QLabel#muted { color: #a2b6c5; }
QFrame#card { background: #192735; border: 1px solid #2b4050; border-radius: 12px; }
QFrame#card QLabel { background: transparent; }
QPushButton { background: #223747; border: 1px solid #3b5567; border-radius: 6px; padding: 9px 14px; }
QPushButton:hover { background: #304e61; }
QPushButton:disabled { color: #70828e; }
QPushButton#primary { background: #24876e; border-color: #36bf91; font-weight: 600; }
QLineEdit, QSpinBox, QTextEdit, QTableWidget { background: #192735; border: 1px solid #3b5567; border-radius: 4px; padding: 6px; }
QProgressBar { background: #2b3c49; border: none; border-radius: 5px; min-height: 10px; max-height: 10px; }
QProgressBar::chunk { background: #36bf91; border-radius: 5px; }
QTabWidget::pane { border: 1px solid #304656; }
QTabBar::tab { background: #192735; padding: 10px 14px; }
QTabBar::tab:selected { background: #2b4557; }
QCheckBox { spacing: 8px; padding: 4px; }
QHeaderView::section { background: #223747; padding: 8px; border: none; }
QMenu { background: #192735; border: 1px solid #3b5567; }
QMenu::item { padding: 8px 18px; }
QMenu::item:selected { background: #304e61; }
"""


def icon_for(state):
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#152430"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(1, 1, 62, 62, 16, 16)
    painter.setBrush(QColor(COLORS.get(state, "#869baa")))
    painter.drawEllipse(15, 15, 34, 34)
    painter.end()
    return QIcon(pix)


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
    def __init__(self, title, subtitle):
        super().__init__()
        self.setObjectName("card")
        self.window = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        top = QHBoxLayout()
        top.addWidget(QLabel(title))
        label = QLabel(subtitle)
        label.setObjectName("eyebrow")
        top.addStretch()
        top.addWidget(label)
        layout.addLayout(top)
        self.remaining = QLabel("残り — %")
        self.remaining.setObjectName("remaining")
        layout.addWidget(self.remaining)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.used = QLabel("使用率：未取得")
        self.used.setObjectName("muted")
        layout.addWidget(self.used)
        self.reset = QLabel("次回リセット：未取得")
        layout.addWidget(self.reset)
        self.count = QLabel("あと —")
        self.count.setObjectName("muted")
        layout.addWidget(self.count)

    def update_rate(self, window, stale=False):
        self.window = window
        self.remaining.setText(f"残り {window.remaining:g}%" if window else "残り — %")
        self.bar.setValue(round(window.remaining) if window else 0)
        color = "#738896" if stale or not window else "#ef7180" if window.remaining == 0 else "#edbd5c" if window.remaining <= 20 else "#36bf91"
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}")
        self.used.setText((f"使用率：{window.used_percent:g}%" if window else "使用率：未取得") + ("  ·  最後に取得した値" if stale else ""))
        self.reset.setText("次回リセット：" + local_date(window.reset_at if window else None))
        self.tick()

    def tick(self):
        self.count.setText(countdown(self.window.reset_at if self.window else None))


class MainWindow(QMainWindow):
    refresh = Signal()
    settings_requested = Signal()
    history_requested = Signal()
    diagnostics_requested = Signal()
    quit_requested = Signal()

    def __init__(self, mock=False):
        super().__init__()
        self.setWindowTitle("Codex Rate Manager" + (" — モック" if mock else ""))
        self.resize(560, 710)
        self.setMinimumWidth(480)
        self.tray_enabled = True
        self.exiting = False
        self.setWindowIcon(icon_for("CONNECTING"))
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        top = QHBoxLayout()
        brand = QLabel("CODEX RATE MANAGER")
        brand.setObjectName("eyebrow")
        top.addWidget(brand)
        top.addStretch()
        small = QLabel("MOCK  /  開発モード" if mock else "RATE MONITOR  /  0.1")
        small.setObjectName("eyebrow")
        top.addWidget(small)
        layout.addLayout(top)
        self.status = QLabel(LABELS["CONNECTING"])
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.description = QLabel("作業を再開できるタイミングをお知らせします。")
        self.description.setObjectName("muted")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.five = RateCard("5時間レート", "5 HOUR WINDOW")
        self.weekly = RateCard("週間レート", "WEEKLY WINDOW")
        layout.addWidget(self.five)
        layout.addWidget(self.weekly)
        self.connection = QLabel("Codex接続：接続待ち")
        self.connection.setWordWrap(True)
        self.connection.setObjectName("muted")
        layout.addWidget(self.connection)
        self.updated = QLabel("最終更新：—")
        self.updated.setObjectName("muted")
        layout.addWidget(self.updated)
        buttons = QHBoxLayout()
        for text, signal in (("今すぐ更新", self.refresh), ("履歴", self.history_requested), ("設定", self.settings_requested), ("診断", self.diagnostics_requested)):
            button = QPushButton(text)
            if text == "今すぐ更新":
                button.setObjectName("primary")
            button.clicked.connect(signal.emit)
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def update_status(self, snapshot, state, detail):
        self.status.setText("● " + LABELS.get(state, state))
        self.status.setStyleSheet(f"color: {COLORS.get(state, '#92a8b6')};")
        self.description.setText("レート残量が少なくなっています。" if state == "LOW" else "接続前の値は利用可能判定に使いません。" if state == "DISCONNECTED" else "両方のレート枠を確認して、復帰をお知らせします。")
        stale = state in ("DISCONNECTED", "CONNECTING", "ERROR", "VERIFYING")
        self.five.update_rate(snapshot.five_hour if snapshot else None, stale)
        self.weekly.update_rate(snapshot.weekly if snapshot else None, stale)
        self.connection.setText("Codex接続：" + detail)
        self.updated.setText("最終更新：" + (local_date(snapshot.fetched_at) if snapshot else "—"))

    def show_front(self):
        self.showNormal()
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
            self.quit_requested.emit()


class SettingsDialog(QDialog):
    save_requested = Signal(object, object)
    test_requested = Signal(object)
    reconnect = Signal()

    def __init__(self, config, has_webhook, parent, mock=False):
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
        check(general, "tray_enabled", "タスクトレイ常駐（×で格納）")
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
        for kind, title in (("low", "残量低下"), ("limit", "上限到達"), ("reset", "利用可能への復帰")):
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
        check(discord, "discord_enabled", "Discord通知を有効にする")
        self.webhook = QLineEdit()
        self.webhook.setEchoMode(QLineEdit.EchoMode.Password)
        self.webhook.setPlaceholderText("登録済み（変更する場合のみ入力）" if has_webhook else "https://discord.com/api/webhooks/…")
        discord.addRow("Webhook URL", self.webhook)
        self.clear_webhook = QCheckBox("登録済みWebhookを削除")
        discord.addRow(self.clear_webhook)
        for kind, title in (("low", "残量低下"), ("limit", "上限到達"), ("reset", "利用可能への復帰")):
            check(discord, "discord_" + kind, title)
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
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("閉じる")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

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
        values["reminders"] = [minute for minute, box in self.reminders.items() if box.isChecked()]
        values["codex_path"] = self.codex_path.text().strip()
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.feedback.setText("設定を保存しています...")
        self.save_requested.emit(replace(self.config, **values), self.value_webhook())

    def result(self, kind, success, message):
        self.feedback.setText(message)
        self.test_button.setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)


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
            kinds = {"low": "残量低下", "limit": "上限到達", "reset": "利用可能への復帰", "reminder": "事前通知", "test": "テスト"}
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
