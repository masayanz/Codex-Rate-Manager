import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QWidget

from codex_rate_manager.window_position import WindowPositionManager, clamp_rect


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_clamp_rect_handles_negative_monitor_coordinates():
    area = QRect(-1920, 0, 1920, 1080)
    assert clamp_rect(QRect(-2500, -100, 600, 500), area) == QRect(-1920, 0, 600, 500)


def test_clamp_rect_keeps_title_bar_inside_available_geometry():
    area = QRect(0, 40, 800, 560)
    assert clamp_rect(QRect(300, 0, 400, 400), area).top() == 40
    assert clamp_rect(QRect(700, 300, 400, 400), area) == QRect(400, 200, 400, 400)


def test_clamp_rect_resizes_window_larger_than_screen():
    assert clamp_rect(QRect(-10, -10, 1200, 900), QRect(0, 0, 800, 600)) == QRect(0, 0, 800, 600)


def test_restore_falls_back_to_primary_for_removed_screen(qapp):
    window = QWidget()
    window.resize(320, 240)
    manager = WindowPositionManager(window)
    area = qapp.primaryScreen().availableGeometry()
    restored = manager.restore({"x": -1800, "y": -900, "width": 320, "height": 240, "screen": "removed-monitor"})
    assert area.intersects(restored)
    assert restored.left() >= area.left()
    assert restored.top() >= area.top()
    manager.deleteLater()
    window.deleteLater()


def test_missing_screen_uses_primary_right_centered_position(qapp):
    window = QWidget()
    window.resize(320, 240)
    manager = WindowPositionManager(window)
    restored = manager.restore({"x": -5000, "y": -5000, "width": 320, "height": 240, "screen": "gone"})
    area = qapp.primaryScreen().availableGeometry()
    assert restored.right() == area.right() - 24
    assert restored.center().y() == area.center().y()
    manager.deleteLater()
    window.deleteLater()


def test_ensure_visible_clamps_actual_top_level_frame(qapp):
    window = QWidget()
    window.resize(320, 240)
    manager = WindowPositionManager(window)
    area = qapp.primaryScreen().availableGeometry()
    window.move(area.right() + 200, area.bottom() + 200)
    manager.ensure_visible()
    frame = window.frameGeometry()
    assert frame.left() >= area.left()
    assert frame.top() >= area.top()
    assert frame.right() <= area.right()
    assert frame.bottom() <= area.bottom()
    manager.deleteLater()
    window.deleteLater()


def test_empty_restore_places_initial_frame_without_using_client_size(qapp):
    window = QWidget()
    window.resize(320, 240)
    manager = WindowPositionManager(window)
    restored = manager.restore({})
    area = qapp.primaryScreen().availableGeometry()
    assert restored.right() == area.right() - 24
    assert restored.center().y() == area.center().y()
    manager.deleteLater()
    window.deleteLater()


def test_fully_offscreen_window_uses_primary_screen(qapp):
    window = QWidget()
    window.resize(320, 240)
    manager = WindowPositionManager(window)
    window.move(-5000, -5000)
    restored = manager.ensure_visible()
    area = qapp.primaryScreen().availableGeometry()
    assert area.intersects(restored)
    assert restored.left() >= area.left()
    assert restored.top() >= area.top()
    manager.deleteLater()
    window.deleteLater()


def test_bar_restore_normalizes_legacy_size(qapp):
    window = QWidget()
    manager = WindowPositionManager(window)
    restored = manager.restore_geometry("bar", {"x": 80, "y": 90, "width": 850, "height": 96, "screen": ""})
    assert restored.width() == 450
    assert restored.height() == 48
    manager.deleteLater()
    window.deleteLater()
