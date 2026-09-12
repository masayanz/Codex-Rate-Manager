"""利用者マニュアル用に実UIをサンプルデータで描画する（通信・設定保存なし）。"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1.5"

from pathlib import Path
import time
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtWidgets import QApplication, QTabWidget
from codex_rate_manager.ui import MainWindow, SettingsDialog, HistoryDialog, DiagnosticsDialog
from codex_rate_manager.storage import Config
from codex_rate_manager.state import Snapshot, RateWindow

output = Path(__file__).resolve().parents[1] / "docs/manual/images"
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
for name in ("meiryo.ttc", "meiryob.ttc", "segoeui.ttf", "segoeuib.ttf"):
    QFontDatabase.addApplicationFont(str(fonts / name))
app.setFont(QFont("Meiryo", 9))
app.setQuitOnLastWindowClosed(False)
window = MainWindow()
now = time.time()
snapshot = Snapshot(RateWindow(28, now + 3 * 3600, 300), RateWindow(46, now + 3 * 86400, 10080), fetched_at=now)
window.update_status(snapshot, "AVAILABLE", "正常（サンプル）")

def capture(widget, name):
    widget.show()
    QTest.qWait(400)
    assert widget.grab().save(str(output / f"{name}.png"))

capture(window, "main")
for skin in ("graphite", "minimal_dark"):
    window.skins.apply(skin)
    capture(window, skin)
window.skins.apply("neon_future")
window.screenshot_message.setText("画面をクリップボードへコピーしました")
capture(window, "screenshot-success")
window.screenshot_message.clear()
window.update_status(Snapshot(RateWindow(100, now + 1800, 300), snapshot.weekly, fetched_at=now), "LIMITED_5H", "正常（サンプル）")
capture(window, "limited")
window.update_status(snapshot, "AVAILABLE", "正常（サンプル）")
settings = SettingsDialog(Config(), False, window)
tabs = settings.findChild(QTabWidget)
for index, name in enumerate(("general", "monitor", "notifications", "discord", "codex", "appearance")):
    tabs.setCurrentIndex(index)
    capture(settings, f"settings-{name}")
settings.hide()
history = HistoryDialog(window)
history.fill("rates", ([], [("2026/09/12 15:00:00", 28, "72%", None, 46, "54%", None, "AVAILABLE"), ("2026/09/12 14:55:00", 100, "0%", None, 46, "54%", None, "LIMITED_5H")]))
capture(history, "history")
history.hide()
diagnostics = DiagnosticsDialog(window)
diagnostics.update_data({"接続状態": "接続済み（マニュアル用サンプル）", "DB状態": "正常", "5時間枠": "残量 72%", "週間枠": "残量 54%"})
capture(diagnostics, "diagnostics")
diagnostics.hide()
window.exiting = True
window.close()
print(f"Saved {len(list(output.glob('*.png')))} images to {output}")
