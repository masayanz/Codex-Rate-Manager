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
    assert tabs is not None and tabs.count() == 5
    settings.fields["normal_interval"].setValue(120)
    settings.buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert wait_for(app.app, lambda: app.config.normal_interval == 120)
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
