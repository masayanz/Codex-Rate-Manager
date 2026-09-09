import time

from codex_rate_manager.monitor import Monitor
from codex_rate_manager.state import State


def test_disconnect_backoff_and_recovery(tmp_path):
    monitor = Monitor(tmp_path, mock=True)
    monitor.mock_state = "DISCONNECTED"
    now = time.time()
    monitor.fetch()
    assert monitor.state == State.DISCONNECTED
    assert 29 <= monitor.next_fetch - now <= 32
    monitor.fetch()
    assert 59 <= monitor.next_fetch - now <= 62
    monitor.fetch()
    assert 119 <= monitor.next_fetch - now <= 122
    monitor.mock_state = "AVAILABLE"
    monitor.fetch()
    assert monitor.state == State.AVAILABLE
    assert monitor.backoff == 30


def test_resume_requests_immediate_fetch_without_claiming_available(tmp_path):
    monitor = Monitor(tmp_path, mock=True)
    monitor.state = State.DISCONNECTED
    monitor.next_fetch = time.time() + 120
    monitor.handle("resume", None)
    assert monitor.next_fetch == 0
    assert monitor.state == State.DISCONNECTED


def test_missing_required_window_reconnects_with_backoff(tmp_path):
    monitor = Monitor(tmp_path, mock=True)
    monitor.mock_state = "AVAILABLE"
    monitor.mock_payload = lambda: {
        "rateLimits": {
            "primary": {"usedPercent": 10, "windowDurationMins": 300},
        }
    }
    now = time.time()
    monitor.fetch()
    assert monitor.state == State.DISCONNECTED
    assert 29 <= monitor.next_fetch - now <= 32
    assert monitor.snapshot is None
