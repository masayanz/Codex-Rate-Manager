"""Persistence and visibility handling for the main window."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QEvent, QRect, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget


def clamp_rect(rect: QRect, available: QRect) -> QRect:
    """Fit *rect* inside *available*, including its complete title bar."""
    if not available.isValid():
        return QRect(rect)
    width = min(max(1, rect.width()), available.width())
    height = min(max(1, rect.height()), available.height())
    x = max(available.left(), min(rect.x(), available.right() - width + 1))
    y = max(available.top(), min(rect.y(), available.bottom() - height + 1))
    return QRect(x, y, width, height)


def _screen_name(screen: QScreen | None) -> str:
    return screen.name() if screen is not None else ""


class WindowPositionManager(QObject):
    """Keep a window's saved rectangle on a currently usable monitor."""

    changed = Signal(dict)

    def __init__(self, window: QWidget, debounce_ms: int = 180, parent: QObject | None = None):
        super().__init__(parent or window)
        self.window = window
        self._last: dict[str, Any] | None = None
        self._normal_frame: QRect | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._emit_capture)
        window.installEventFilter(self)

        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._screen_added)
            app.screenRemoved.connect(self._screen_removed)
            for screen in app.screens():
                self._watch_screen(screen)

        # A new window starts on the primary display with a predictable offset.
        # ``restore`` may subsequently replace this with the persisted position.
        self._place_initial()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.window and event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self.ensure_visible)
        if watched is self.window and event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(0, self.ensure_visible)
        if watched is self.window and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            if not self.window.isMinimized() and not self.window.isMaximized():
                self._normal_frame = self.window.frameGeometry()
                self._timer.start()
        return super().eventFilter(watched, event)

    def _screen_added(self, screen: QScreen) -> None:
        self._watch_screen(screen)
        self.ensure_visible()

    def _screen_removed(self, _screen: QScreen) -> None:
        self.ensure_visible()

    def _watch_screen(self, screen: QScreen) -> None:
        screen.availableGeometryChanged.connect(self._screen_changed)
        screen.logicalDotsPerInchChanged.connect(self._screen_changed)

    def _screen_changed(self, *_args: object) -> None:
        self.ensure_visible()

    @staticmethod
    def _screens() -> list[QScreen]:
        app = QGuiApplication.instance()
        return app.screens() if app is not None else []

    def _primary(self) -> QScreen | None:
        app = QGuiApplication.instance()
        return app.primaryScreen() if app is not None else None

    def _screen_for_rect(self, rect: QRect) -> QScreen | None:
        screens = self._screens()
        if not screens:
            return None
        def overlap(screen: QScreen) -> int:
            intersection = rect.intersected(screen.availableGeometry())
            return intersection.width() * intersection.height() if intersection.isValid() else 0
        scores = [overlap(screen) for screen in screens]
        if max(scores, default=0) == 0:
            return self._primary() or screens[0]
        return screens[scores.index(max(scores))]

    def _frame_margins(self) -> tuple[int, int, int, int]:
        frame = self.window.frameGeometry()
        geometry = self.window.geometry()
        return (geometry.x() - frame.x(), geometry.y() - frame.y(),
                frame.right() - geometry.right(), frame.bottom() - geometry.bottom())

    def _set_frame_geometry(self, frame: QRect) -> QRect:
        left, top, right, bottom = self._frame_margins()
        client = QRect(frame.x() + left, frame.y() + top,
                       max(1, frame.width() - left - right),
                       max(1, frame.height() - top - bottom))
        self.window.setGeometry(client)
        return self.window.frameGeometry()

    def _place_initial(self) -> None:
        screen = self._primary()
        if screen is None:
            return
        area = screen.availableGeometry()
        frame = self.window.frameGeometry()
        x = area.right() - frame.width() + 1 - 24
        y = area.top() + max(0, (area.height() - frame.height()) // 2)
        self._set_frame_geometry(clamp_rect(QRect(x, y, frame.width(), frame.height()), area))

    def restore(self, value: dict[str, Any] | None) -> QRect:
        """Restore a saved rectangle and return the rectangle actually applied."""
        value = value or {}
        if not value:
            self._place_initial()
            self._last = self.capture()
            return self.window.frameGeometry()
        try:
            width = int(value.get("width", self.window.width()))
            height = int(value.get("height", self.window.height()))
            x = int(value.get("x", self.window.x()))
            y = int(value.get("y", self.window.y()))
        except (TypeError, ValueError):
            return self.ensure_visible()
        rect = QRect(x, y, width, height)
        wanted = str(value.get("screen", ""))
        screens = self._screens()
        screen = next((item for item in screens if _screen_name(item) == wanted), None)
        missing = screen is None and bool(wanted)
        screen = screen or self._primary() or (screens[0] if screens else None)
        if screen is not None:
            if missing:
                area = screen.availableGeometry()
                rect = QRect(area.right() - width + 1 - 24,
                             area.top() + max(0, (area.height() - height) // 2), width, height)
            rect = clamp_rect(rect, screen.availableGeometry())
        self._set_frame_geometry(rect)
        self._last = self.capture()
        return self.window.frameGeometry()

    def save_geometry(self, mode: str) -> dict[str, Any]:
        return self.capture()

    def restore_geometry(self, mode: str, value: dict[str, Any] | None = None) -> QRect:
        if mode == "bar" and value:
            value = {**value, "width": 450, "height": 48}
        if value:
            return self.restore(value)
        default = self.get_default_geometry(mode)
        screen = self._screen_for_rect(default) or self._primary()
        if screen is not None:
            default = clamp_rect(default, screen.availableGeometry())
        result = self._set_frame_geometry(default)
        self._last = self.capture()
        return result

    def get_default_geometry(self, mode: str) -> QRect:
        screen = self._primary()
        area = screen.availableGeometry() if screen else QRect()
        sizes = {"standard": (520, 720), "bar": (450, 48), "mini": (240, 120)}
        width, height = sizes.get(mode, sizes["standard"])
        if mode == "bar":
            return QRect(area.center().x() - width // 2, area.top() + 20, width, height)
        if mode == "mini":
            return QRect(area.right() - width - 20, area.bottom() - height - 20, width, height)
        return QRect(area.right() - width - 24, area.top() + max(0, (area.height() - height) // 2), width, height)

    def ensure_visible(self) -> QRect:
        if self.window.isMinimized() or self.window.isMaximized():
            return self.window.frameGeometry()
        current = self.window.frameGeometry()
        screen = self._screen_for_rect(current)
        rect = clamp_rect(current, screen.availableGeometry() if screen else QRect())
        if rect != current:
            return self._set_frame_geometry(rect)
        return current

    def capture(self) -> dict[str, Any]:
        rect = self._normal_frame if (self.window.isMinimized() or self.window.isMaximized()) and self._normal_frame else self.window.frameGeometry()
        return {"x": rect.x(), "y": rect.y(), "width": rect.width(), "height": rect.height(), "screen": _screen_name(self.window.screen())}

    def _emit_capture(self) -> None:
        self.ensure_visible()
        value = self.capture()
        if value != self._last:
            self._last = value
            self.changed.emit(value)
