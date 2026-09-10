"""Developer-only, non-blocking verification of the tray visibility workflow.

The parent application deliberately owns all tray and persistence behavior.  This
module only drives that public UI surface when explicitly requested with the
mock/verification command-line flags.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox, QDialogButtonBox, QSystemTrayIcon


class _TrayVerification:
    def __init__(self, controller):
        self.controller = controller
        self.started = time.monotonic()
        self.step = 0
        self.report = {
            "success": False,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "startup": {},
            "states": {},
            "errors": [],
        }
        self.timer = QTimer(controller.app)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.advance)
        self.timer.start()

    @property
    def app(self):
        return self.controller.app

    @property
    def data_dir(self) -> Path:
        return Path(self.controller.data_dir)

    def record(self, name, value=True):
        self.report["states"][name] = value

    def fail(self, message):
        self.report["errors"].append(str(message))
        self.finish(False)

    def wait_ready(self):
        c = self.controller
        return bool(c.config_loaded and c.settings is not None)

    def find_button(self, dialog, text):
        for button in dialog.buttons():
            if button.text() == text:
                return button
        return None

    def click_close_choice(self, text):
        dialog = self.controller.close_dialog
        if not dialog or not dialog.isVisible():
            return False
        button = self.find_button(dialog, text)
        if button is None:
            self.fail(f"close dialog button not found: {text}")
            return False
        button.click()
        return True

    def advance(self):
        if time.monotonic() - self.started > 20:
            self.fail("verification timed out")
            return
        try:
            self._advance()
        except Exception as exc:  # keep developer verification from breaking the app
            self.fail(f"{type(exc).__name__}: {exc}")

    def _advance(self):
        c = self.controller
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.fail("system tray is unavailable")
            return

        if self.step == 0:
            self.report["startup"] = {
                "config_loaded": c.config_loaded,
                "tray_enabled": bool(getattr(c.config, "tray_enabled", False)),
                "tray_visible": c.tray.isVisible(),
                "monitor_alive": c.monitor.is_alive(),
                "tray_available": True,
            }
            if not c.config_loaded:
                return
            if c.latest_state == "CONNECTING":
                return
            self.record("startup_config_recorded")
            c.open_settings()
            self.step = 1
            return

        if self.step == 1:
            if not self.wait_ready():
                return
            settings = c.settings
            settings.grab().save(str(self.data_dir / "tray-verification-settings.png"))
            box = settings.fields.get("tray_enabled")
            if box is None:
                self.fail("settings tray_enabled field missing")
                return
            box.setChecked(False)
            if c.tray.isVisible():
                self.fail("tray did not hide immediately")
                return
            self.step = 2
            return

        if self.step == 2:
            if c.tray_pending:
                return
            if c.tray.isVisible():
                self.fail("tray remained visible after disabling")
                return
            self.record("off_immediate", True)
            self.record("monitor_alive_off", c.monitor.is_alive())
            if not c.monitor.is_alive():
                self.fail("monitor stopped while tray was hidden")
                return
            c.settings.close()
            c.window.close()
            self.step = 3
            return

        if self.step == 3:
            dialog = c.close_dialog
            if not dialog or not dialog.isVisible():
                return
            dialog.grab().save(str(self.data_dir / "tray-verification-safety.png"))
            self.record("safety_dialog", True)
            if not self.click_close_choice("キャンセル"):
                return
            self.step = 4
            return

        if self.step == 4:
            if c.close_dialog and c.close_dialog.isVisible():
                return
            c.window.close()
            self.step = 5
            return

        if self.step == 5:
            dialog = c.close_dialog
            if not dialog or not dialog.isVisible():
                return
            if not self.click_close_choice("タスクトレイを表示して閉じる"):
                return
            self.step = 6
            return

        if self.step == 6:
            if c.tray_pending:
                return
            if not c.tray.isVisible() or c.window.isVisible():
                return
            self.record("show_then_hide", True)
            self.record("config_true_after_close", bool(c.config.tray_enabled))
            c.window.show_front()
            if not c.window.isVisible():
                self.fail("show_front did not restore the window")
                return
            c.open_settings()
            self.step = 7
            return

        if self.step == 7:
            if not self.wait_ready():
                return
            box = c.settings.fields.get("tray_enabled")
            if box is None:
                self.fail("settings tray_enabled field missing on second open")
                return
            box.setChecked(False)
            self.step = 8
            return

        if self.step == 8:
            if c.tray_pending or c.tray.isVisible():
                return
            self.record("off_again", True)
            c.settings.fields["tray_enabled"].setChecked(True)
            self.step = 9
            return

        if self.step == 9:
            if c.tray_pending or not c.tray.isVisible():
                return
            tooltip = c.tray.toolTip()
            icon_ok = not c.tray.icon().isNull()
            self.record("on_again", True)
            self.record("dynamic_icon", icon_ok)
            self.record("tooltip_values", "5時間:" in tooltip and "週間:" in tooltip)
            if not icon_ok or "5時間:" not in tooltip or "週間:" not in tooltip:
                self.fail("dynamic tray icon or tooltip values missing")
                return
            self.previous_icon = c.tray.icon().pixmap(32, 32).toImage()
            c.monitor.command("mock", "LOW")
            self.step = 10
            return

        if self.step == 10:
            if c.latest_state != "LOW":
                return
            if c.tray.icon().pixmap(32, 32).toImage() == self.previous_icon:
                self.fail("meter did not update after rate change")
                return
            self.record("dynamic_meter_updated")
            c.settings.close()
            self.persist_and_finish()

    def persist_and_finish(self):
        config_path = self.data_dir / "config.json"
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.report["persisted"] = {"tray_enabled": data.get("tray_enabled")}
            if data.get("tray_enabled") is not True:
                self.fail("tray_enabled was not persisted as true")
                return
        except Exception as exc:
            self.fail(f"could not read persisted config: {exc}")
            return
        self.report["success"] = True
        self.finish(True)

    def finish(self, success):
        if getattr(self, "finished", False):
            return
        self.finished = True
        self.report["success"] = bool(success)
        self.report["duration_ms"] = round((time.monotonic() - self.started) * 1000)
        self.timer.stop()
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            (self.data_dir / "tray-verification.json").write_text(
                json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        finally:
            QTimer.singleShot(0, self.controller.quit)


def start_tray_verification(controller):
    """Start the asynchronous developer harness and retain it on the controller."""
    if getattr(controller, "tray_verification", None) is not None:
        return controller.tray_verification
    controller.tray_verification = _TrayVerification(controller)
    return controller.tray_verification
