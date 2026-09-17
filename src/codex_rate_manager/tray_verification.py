"""Developer-only verification for the application-owned tray lifecycle."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QSystemTrayIcon


class _TrayVerification:
    def __init__(self, controller):
        self.controller = controller
        self.started = time.monotonic()
        self.original_tray = controller.tray
        self.step = 0
        self.report = {"success": False, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "startup": {}, "states": {}, "errors": []}
        self.timer = QTimer(controller.app)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.advance)
        self.timer.start()

    @property
    def data_dir(self) -> Path:
        return Path(self.controller.data_dir)

    def record(self, name, value=True):
        self.report["states"][name] = value

    def fail(self, message):
        self.report["errors"].append(str(message))
        self.finish(False)

    def advance(self):
        if time.monotonic() - self.started > 20:
            self.fail("verification timed out")
            return
        try:
            self._advance()
        except Exception as exc:
            self.fail(f"{type(exc).__name__}: {exc}")

    def _advance(self):
        c = self.controller
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.fail("system tray is unavailable")
            return
        if self.step == 0:
            self.report["startup"] = {"config_loaded": c.config_loaded, "tray_visible": c.tray.isVisible(), "tray_available": True, "same_instance": c.tray is self.original_tray}
            if not c.tray.isVisible():
                self.fail("tray was not visible immediately after startup")
                return
            self.record("startup_visible")
            self.step = 1
            return
        if self.step == 1:
            if not c.config_loaded or c.latest_state == "CONNECTING":
                return
            self.record("same_instance_after_config", c.tray is self.original_tray)
            for mode in ("standard", "bar", "mini", "standard"):
                c.set_display_mode(mode)
                if c.tray is not self.original_tray or not c.tray.isVisible():
                    self.fail(f"tray changed during mode {mode}")
                    return
            self.record("same_instance_after_modes")
            self.step = 2
            return
        if self.step == 2:
            c.window.close()
            if c.window.isVisible() or c.tray is not self.original_tray or not c.tray.isVisible():
                self.fail("tray/window close invariant failed")
                return
            self.record("same_instance_after_close")
            c.window.show_front()
            self.previous_icon = c.tray.icon().pixmap(32, 32).toImage()
            c.monitor.command("mock", "LOW")
            self.step = 3
            return
        if self.step == 3:
            if c.latest_state != "LOW":
                return
            tooltip = c.tray.toolTip()
            meter_updated = c.tray.icon().pixmap(32, 32).toImage() != self.previous_icon
            tooltip_ok = "5時間:" in tooltip and "週間:" in tooltip and "状態:" in tooltip
            self.record("same_instance_after_rate_update", c.tray is self.original_tray)
            self.record("dynamic_meter_updated", meter_updated)
            self.record("tooltip_values", tooltip_ok)
            if c.tray is not self.original_tray or not meter_updated or not tooltip_ok:
                self.fail("tray update invariant failed")
                return
            self.finish(True)

    def finish(self, success):
        if getattr(self, "finished", False):
            return
        self.finished = True
        self.report["success"] = bool(success)
        self.report["duration_ms"] = round((time.monotonic() - self.started) * 1000)
        self.timer.stop()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "tray-verification.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        QTimer.singleShot(0, self.controller.quit)


def start_tray_verification(controller):
    if getattr(controller, "tray_verification", None) is None:
        controller.tray_verification = _TrayVerification(controller)
    return controller.tray_verification
