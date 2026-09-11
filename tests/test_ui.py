import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time

from types import SimpleNamespace
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QTabWidget

from codex_rate_manager.main import Application


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


@pytest.fixture
def running_app(qapp, tmp_path, monkeypatch):
    import codex_rate_manager.monitor as monitor_module
    monkeypatch.setattr(monitor_module, "send_windows", lambda *args: (False, "mocked"))
    monkeypatch.setattr(monitor_module.DiscordClient, "send", lambda *args: (True, "mocked"))
    server = QLocalServer()
    name = f"codex-rate-test-{tmp_path.name}"
    assert server.listen(name)
    args = SimpleNamespace(mock=True, smoke_test=None)
    application = Application(qapp, args, tmp_path, server)
    for _ in range(60):
        qapp.processEvents()
        time.sleep(0.05)
        if application.latest_state != "CONNECTING":
            break
    yield application
    application.monitor.stop()
    application.monitor.join(timeout=3)
    application.timer.stop()
    qapp.removeNativeEventFilter(application.resume_filter)
    application.window.exiting = True
    application.window.close()
    application.tray.hide()
    server.close()
    QCoreApplication.processEvents()


def wait_for(app, predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_mock_transitions_and_countdown_stay_responsive(running_app):
    app = running_app
    assert app.latest_state == "AVAILABLE"
    app.monitor.command("mock", "LIMITED_5H")
    app.monitor.command("refresh")
    assert wait_for(app.app, lambda: app.latest_state == "LIMITED_5H")
    assert app.latest_state == "LIMITED_5H"
    app.monitor.command("mock", "LIMITED_WEEKLY")
    app.monitor.command("refresh")
    assert wait_for(app.app, lambda: app.latest_state == "LIMITED_WEEKLY")
    assert app.latest_state == "LIMITED_WEEKLY"
    app.monitor.command("mock", "RESET")
    app.monitor.command("refresh")
    assert wait_for(app.app, lambda: app.latest_state == "AVAILABLE")
    assert app.latest_state == "AVAILABLE"
    app.tick()
    assert app.window.weekly.count.text()
    assert app.window.five.remaining.text() == "残り 100%"


def test_settings_tabs_save_and_history_diagnostics(running_app):
    app = running_app
    app.open_settings()
    settings = app.settings
    assert settings is not None and settings.isVisible()
    tabs = settings.findChild(QTabWidget)
    assert tabs is not None and tabs.count() == 6
    settings.tray_style.setCurrentIndex(1)
    settings.fields["normal_interval"].setValue(120)
    settings.buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert wait_for(app.app, lambda: app.config.normal_interval == 120)
    assert app.config.tray_style == "bars"
    settings.close()
    app.open_history()
    assert wait_for(app.app, lambda: app.history.tables["rates"].rowCount() > 0)
    assert app.history.tables["rates"].columnCount() == 4
    app.open_diagnostics()
    assert app.diagnostics is not None
    app.diagnostics.mock_changed.emit("LOW")
    app.monitor.command("refresh")
    assert wait_for(app.app, lambda: app.latest_state == "LOW")


def test_notification_channels_are_independent(running_app):
    app = running_app
    config = replace(app.config, discord_enabled=True)
    app.monitor.command("save", (config, None))
    assert wait_for(app.app, lambda: app.config.discord_enabled)
    app.open_history()
    app.monitor.command("mock", "LIMITED_5H")
    assert wait_for(app.app, lambda: app.latest_state == "LIMITED_5H")
    def both_results():
        app.monitor.command("history", "notifications")
        table = app.history.tables["notifications"]
        return table.rowCount() >= 2
    assert wait_for(app.app, both_results)
    table = app.history.tables["notifications"]
    results = {table.item(i, 2).text(): table.item(i, 3).text() for i in range(table.rowCount())}
    assert results == {"WINDOWS": "失敗", "DISCORD": "成功"}


def test_close_hides_to_tray_and_show_front(running_app):
    app = running_app
    if not app.tray.isSystemTrayAvailable():
        pytest.skip("offscreen test environment has no system tray")
    app.window.close()
    assert not app.window.isVisible()
    app.window.show_front()
    assert app.window.isVisible()


@pytest.mark.parametrize("mode", ["rings", "bars"])
def test_tray_meter_distinguishes_windows_and_disconnect(qapp, mode):
    from codex_rate_manager.meters import tray_icon
    images = [tray_icon(a, b, "AVAILABLE", mode).pixmap(16, 16).toImage()
              for a, b in [(100, 100), (50, 50), (20, 80), (0, 80), (80, 0)]]
    assert all(not image.isNull() for image in images)
    assert all(images[i] != images[j] for i in range(len(images)) for j in range(i))
    disconnected = tray_icon(80, 0, "DISCONNECTED", mode).pixmap(16, 16).toImage()
    assert disconnected != images[-1]
    assert disconnected == tray_icon(100, 100, "DISCONNECTED", mode).pixmap(16, 16).toImage()


def test_tray_current_values_and_stale_labels(running_app):
    app = running_app
    assert "5時間:" in app.tray.toolTip() and "週間リセット:" in app.tray.toolTip()
    assert "%" in app.tray_five.text()
    app.update(app.latest_snapshot, "DISCONNECTED", "再接続中")
    assert "最終取得値" in app.tray_five.text()
    assert app.window.updated.text().startswith("最終成功：")
    assert app.window.five.gauge.value is None


def test_skin_preview_save_and_cancel(running_app):
    app = running_app
    original = app.tray.icon().pixmap(32, 32).toImage()
    app.open_settings()
    app.settings.skin_select.setCurrentIndex(app.settings.skin_select.findData("graphite"))
    assert app.skins.current_skin_id == "graphite"
    assert app.tray.icon().pixmap(32, 32).toImage() != original
    app.settings.reject()
    assert app.skins.current_skin_id == "neon_future"
    app.open_settings()
    app.settings.skin_select.setCurrentIndex(app.settings.skin_select.findData("minimal_dark"))
    app.settings.fields["animation_enabled"].setChecked(False)
    app.settings.save()
    assert wait_for(app.app, lambda: app.config.skin_id == "minimal_dark")
    app.settings.reject()
    assert app.skins.current_skin_id == "minimal_dark"
    from codex_rate_manager.storage import load_config
    saved = load_config(app.data_dir)
    assert saved.skin_id == "minimal_dark" and not saved.animation_enabled


def test_toolbar_stays_outside_scroll_area_and_position_is_saved(running_app):
    app = running_app
    app.window.resize(480, 450)
    app.app.processEvents()
    assert not app.window.scroll.isAncestorOf(app.window.toolbar)
    assert app.window.toolbar.geometry().bottom() <= app.window.centralWidget().height()
    value = app.positions.capture()
    app.monitor.command("window", value)
    from codex_rate_manager.storage import load_config
    assert wait_for(app.app, lambda: load_config(app.data_dir).window == value)


def test_animation_ends_and_can_be_disabled(qapp):
    from codex_rate_manager.skin_manager import SkinManager
    from codex_rate_manager.meters import RingMeter
    from PySide6.QtCore import QAbstractAnimation
    skins = SkinManager()
    gauge = RingMeter(skins)
    gauge.show()
    gauge.set_value(42)
    gauge.set_value(97)
    assert gauge.animation.state() == QAbstractAnimation.State.Running
    assert wait_for(qapp, lambda: gauge.animation.state() == QAbstractAnimation.State.Stopped)
    assert gauge.display_value == 97
    skins.apply("neon_future", animation_enabled=False)
    gauge.set_value(20)
    assert gauge.display_value == 20
    gauge.close()


def click_close_choice(app, text):
    app.window.close()
    assert app.close_dialog.isVisible()
    button = next(b for b in app.close_dialog.buttons() if b.text() == text)
    button.click()


def test_tray_visibility_immediate_persistent_and_monitor_continues(running_app):
    app = running_app
    app.open_settings()
    app.settings.fields["tray_enabled"].setChecked(False)
    assert not app.tray.isVisible() and app.window.isVisible()
    assert wait_for(app.app, lambda: not app.tray_pending)
    from codex_rate_manager.storage import load_config
    assert not load_config(app.data_dir).tray_enabled
    app.monitor.command("mock", "LOW")
    assert wait_for(app.app, lambda: app.latest_state == "LOW")
    app.settings.fields["tray_enabled"].setChecked(True)
    assert app.tray.isVisible()
    assert wait_for(app.app, lambda: not app.tray_pending)
    assert load_config(app.data_dir).tray_enabled
    assert "5時間:" in app.tray.toolTip() and "週間:" in app.tray.toolTip()


def test_close_hidden_tray_cancel_and_enable_then_hide(running_app, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    app = running_app
    app.request_tray_visibility(False)
    assert wait_for(app.app, lambda: not app.tray_pending)
    click_close_choice(app, "キャンセル")
    assert app.window.isVisible() and app.monitor.is_alive()
    click_close_choice(app, "タスクトレイを表示して閉じる")
    assert app.window.isVisible()  # cannot hide before worker confirms persistence
    assert wait_for(app.app, lambda: not app.tray_pending)
    assert app.tray.isVisible() and not app.window.isVisible()
    from codex_rate_manager.storage import load_config
    assert load_config(app.data_dir).tray_enabled
    app.request_tray_visibility(False)
    assert app.window.isVisible() and not app.tray.isVisible()
    assert wait_for(app.app, lambda: not app.tray_pending)


def test_close_hidden_tray_exit(running_app):
    app = running_app
    app.request_tray_visibility(False)
    assert wait_for(app.app, lambda: not app.tray_pending)
    click_close_choice(app, "アプリを終了")
    assert wait_for(app.app, lambda: not app.monitor.is_alive())
    assert app.quitting


def test_tray_save_failure_keeps_window_accessible(running_app, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon
    import codex_rate_manager.monitor as module
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    app = running_app
    app.args.smoke_test = 1  # suppress unrelated error popup in this test
    app.request_tray_visibility(False)
    assert wait_for(app.app, lambda: not app.tray_pending)
    def fail(*args):
        raise OSError("read only")
    monkeypatch.setattr(module, "save_config", fail)
    click_close_choice(app, "タスクトレイを表示して閉じる")
    assert wait_for(app.app, lambda: not app.tray_pending)
    assert app.window.isVisible() and not app.tray.isVisible()
    assert not app.config.tray_enabled


def test_windows_taskbar_settings_uri(running_app, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: urls.append(url.toString()) or True)
    running_app.open_settings()
    running_app.settings.taskbar_settings.click()
    assert urls == ["ms-settings:taskbar"]
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: False)
    running_app.settings.taskbar_settings.click()
    assert "右クリック" in running_app.settings.feedback.text()


def test_discord_toggle_save_and_reopen(running_app):
    from codex_rate_manager.storage import load_config
    app = running_app
    for enabled in (True, False, True):
        app.open_settings()
        settings = app.settings
        settings.fields["discord_enabled"].setChecked(enabled)
        settings.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        assert wait_for(app.app, lambda: app.config.discord_enabled == enabled and settings.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled())
        assert load_config(app.data_dir).discord_enabled == enabled
        settings.reject()
        app.open_settings()
        assert app.settings.fields["discord_enabled"].isChecked() == enabled
        app.settings.reject()


def test_discord_toggle_persists_without_save(running_app):
    from codex_rate_manager.storage import load_config
    app = running_app
    for enabled in (True, False):
        app.open_settings()
        app.settings.fields["discord_enabled"].click()
        assert wait_for(app.app, lambda: app.config.discord_enabled == enabled)
        assert load_config(app.data_dir).discord_enabled == enabled
        app.settings.reject()
    app.open_settings()
    assert not app.settings.fields["discord_enabled"].isChecked()


def test_discord_toggle_save_failure_restores_checkbox(running_app, monkeypatch):
    import codex_rate_manager.monitor as module
    app = running_app
    app.open_settings()
    def fail(*args):
        raise OSError("read only")
    monkeypatch.setattr(module, "save_config", fail)
    app.settings.fields["discord_enabled"].click()
    assert wait_for(app.app, lambda: "保存できません" in app.settings.feedback.text())
    assert not app.config.discord_enabled
    assert not app.settings.fields["discord_enabled"].isChecked()
