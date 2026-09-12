"""メイン画面の画像取得と、PNG・ネイティブ画像形式でのコピー。"""

from PySide6.QtCore import QBuffer, QIODevice, QMimeData, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication


def capture_widget(widget):
    pixmap = widget.grab()
    if pixmap.isNull():
        raise RuntimeError("Empty widget capture")
    return pixmap


def copy_to_clipboard(pixmap):
    if pixmap.isNull():
        raise RuntimeError("Empty clipboard image")
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not pixmap.save(buffer, "PNG"):
        raise RuntimeError("PNG encoding failed")
    mime = QMimeData()
    mime.setData("image/png", buffer.data())
    mime.setImageData(pixmap.toImage())
    clipboard = QApplication.clipboard()
    if clipboard is None:
        raise RuntimeError("Clipboard unavailable")
    clipboard.setMimeData(mime)
    if not clipboard.mimeData().hasImage():
        raise RuntimeError("Clipboard image unavailable after copy")


def capture_and_copy(widget):
    pixmap = capture_widget(widget)
    copy_to_clipboard(pixmap)
    return pixmap


class ScreenshotController(QObject):
    failed = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self.capture_timer = QTimer(self)
        self.capture_timer.setSingleShot(True)
        self.capture_timer.timeout.connect(self._capture)
        self.restore_timer = QTimer(self)
        self.restore_timer.setSingleShot(True)
        self.restore_timer.timeout.connect(self._finish)

    def request(self):
        if self.busy or self.window.exiting:
            return
        self.busy = True
        self.was_hidden = not self.window.isVisible()
        self.old_state = self.window.windowState()
        self.was_minimized = self.window.isMinimized()
        self.window.screenshot_message.clear()
        if self.was_hidden or self.was_minimized:
            self.window.showNormal()
        self.capture_timer.start(200)

    def _capture(self):
        if self.window.exiting:
            self.busy = False
            return
        try:
            capture_and_copy(self.window)
            message = "画面をクリップボードへコピーしました"
        except Exception as exc:
            # 例外本文には外部データが含まれ得るので、型と発生箇所だけ記録する。
            import traceback
            frames = traceback.extract_tb(exc.__traceback__)
            detail = " > ".join(f"{frame.name}:{frame.lineno}" for frame in frames)
            self.failed.emit(f"{type(exc).__name__} ({detail})")
            message = "画面のコピーに失敗しました"
        self.window.screenshot_message.setText(message)
        self.restore_timer.start(1800)

    def _finish(self):
        self.window.screenshot_message.clear()
        if not self.window.exiting:
            if self.was_hidden:
                self.window.hide()
                self.window.setWindowState(self.old_state)
            elif self.was_minimized:
                self.window.setWindowState(self.old_state)
        self.busy = False
