import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from codex_rate_manager.ui import MainWindow
from codex_rate_manager import screenshot_service


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    widget = MainWindow(mock=True)
    widget.show()
    QTest.qWait(50)
    yield widget
    app.clipboard().clear()
    widget.exiting = True
    widget.close()
    widget.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("skin", ["neon_future", "graphite", "minimal_dark"])
def test_copy_image_png_dimensions_and_toolbar(window, skin):
    window.skins.apply(skin)
    dialog = QDialog(window)
    QLabel("secret sentinel", dialog)
    dialog.show()
    QTest.qWait(50)
    expected = window.grab().toImage()
    window.screenshot_button.click()
    QTest.qWait(300)
    mime = QApplication.clipboard().mimeData()
    actual = QImage.fromData(mime.data("image/png"), "PNG")
    assert mime.hasImage()
    assert actual.size() == expected.size()
    assert actual == expected
    assert actual.width() == round(window.width() * window.devicePixelRatioF())
    assert window.screenshot_message.text() == "画面をクリップボードへコピーしました"
    assert window.toolbar.width() >= window.toolbar.minimumSizeHint().width()
    window.screenshot._finish()
    assert not window.screenshot_message.text()
    dialog.close()


@pytest.mark.parametrize("state", ["hidden", "minimized"])
def test_temporary_show_and_restore(window, state):
    if state == "hidden":
        window.hide()
    else:
        window.showMinimized()
    window.screenshot.request()
    assert window.isVisible() and not window.isMinimized()
    window.screenshot.request()
    QTest.qWait(2200)
    assert not window.screenshot.busy
    assert not window.screenshot_message.text()
    assert (not window.isVisible()) if state == "hidden" else window.isMinimized()


def test_failure_preserves_clipboard_and_reports_safe_details(window, monkeypatch):
    QApplication.clipboard().setText("previous clipboard")
    errors = []
    window.screenshot.failed.connect(errors.append)
    def fail(widget):
        raise RuntimeError("secret token must not enter logs")
    monkeypatch.setattr(screenshot_service, "capture_widget", fail)
    window.screenshot.request()
    QTest.qWait(300)
    assert QApplication.clipboard().text() == "previous clipboard"
    assert window.screenshot_message.text() == "画面のコピーに失敗しました"
    assert "RuntimeError" in errors[0] and "secret" not in errors[0]


def test_keyboard_shortcut(window):
    window.activateWindow()
    QTest.qWait(50)
    QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    QTest.qWait(300)
    assert window.screenshot_message.text() == "画面をクリップボードへコピーしました"
