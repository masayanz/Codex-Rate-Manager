from codex_rate_manager.codex import AppServer, find_codex
from codex_rate_manager.state import Engine, RateWindow, Snapshot, State, classify, next_delay, parse_rates


def test_parse_duration_and_remaining_from_rate_limits_by_id():
    snapshot = parse_rates({"rateLimitsByLimitId": {
        "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1_700_000_000},
        "weekly": {"usedPercent": 70, "windowDurationMins": 10080, "resetsAt": 1_700_100_000},
    }})
    assert snapshot.five_hour.remaining == 75
    assert snapshot.weekly.remaining == 30


def test_missing_required_window_fails_closed():
    snapshot = parse_rates({"rateLimits": {"primary": {"usedPercent": 0, "windowDurationMins": 300}}})
    assert snapshot.weekly is None
    assert classify(snapshot) is State.DISCONNECTED


def test_classification_and_engine_recovery_is_confirmed_and_deduplicated():
    limited = Snapshot(RateWindow(100, 10, 300), RateWindow(20, 20, 10080), fetched_at=1)
    available = Snapshot(RateWindow(0, 30, 300), RateWindow(20, 40, 10080), fetched_at=2)
    engine = Engine()
    assert classify(limited) is State.LIMITED_5H
    assert engine.accept(limited)[0].kind == "limit"
    events = engine.accept(available)
    assert any(event.kind == "reset" for event in events)
    assert engine.accept(available) == []


def test_next_delay_does_not_hammer_when_reset_is_overdue():
    snapshot = Snapshot(RateWindow(100, 90, 300), RateWindow(0, 90, 10080), fetched_at=1)
    assert next_delay(snapshot, now=100) == 30
    assert next_delay(snapshot, now=0) == 60


def test_app_server_stop_only_owns_its_process():
    client = AppServer(executable="definitely-not-started")
    assert client.pid is None
    client.stop()
