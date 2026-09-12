from dataclasses import replace
import json

import pytest

from codex_rate_manager.state import Engine, Snapshot, RateWindow
from codex_rate_manager.monitor import Monitor
from codex_rate_manager.storage import Database, Config, load_config


def snapshot(five, weekly, reset=9999999999):
    return Snapshot(RateWindow(100-five, reset, 300) if five is not None else None,
                    RateWindow(100-weekly, reset, 10080) if weekly is not None else None,
                    fetched_at=1789203900)


def recovered(events):
    return [e for e in events if e.recovery]


@pytest.mark.parametrize("previous,current,expected", [
    ((0, 50), (100, 49), ["RATE_5H_RECOVERED"]),
    ((25, 50), (75, 49), ["RATE_5H_RECOVERED"]),
    ((50, 20), (49, 70), ["RATE_WEEKLY_RECOVERED"]),
    ((10, 20), (100, 100), ["RATE_5H_RECOVERED", "RATE_WEEKLY_RECOVERED"]),
    ((80, 70), (79, 69), []),
    ((42, 60), (43, 60), ["RATE_5H_RECOVERED"]),
    (None, (100, 100), []),
    ((34, 29), (100, 100), ["RATE_5H_RECOVERED", "RATE_WEEKLY_RECOVERED"]),
    ((0, 20), (0, 70), ["RATE_WEEKLY_RECOVERED"]),
    ((20, 0), (70, 0), ["RATE_5H_RECOVERED"]),
])
def test_required_cases(previous, current, expected):
    engine = Engine()
    if previous:
        engine.accept(snapshot(*previous))
    events = recovered(engine.accept(snapshot(*current)))
    assert [e.event_type for e in events] == expected
    for e in events:
        assert f"5時間残量：{current[0]}%" in e.message
        assert f"週間残量：{current[1]}%" in e.message
        index = 0 if e.event_type == "RATE_5H_RECOVERED" else 1
        assert e.recovery["previous_remaining"] == previous[index]
        assert e.recovery["current_remaining"] == current[index]
        assert e.recovery["delta"] == current[index] - previous[index]
        assert "確認時刻：2026/" in e.message
    assert recovered(engine.accept(snapshot(*current))) == []


def test_fractional_changes_compare_previous_poll_not_accumulated():
    engine = Engine()
    engine.accept(snapshot(42, 60))
    for value in (42.1, 42.9, 43.8):
        assert recovered(engine.accept(snapshot(value, 60))) == []
    assert recovered(engine.accept(snapshot(44.8, 60)))[0].recovery["delta"] == 1


def test_consecutive_increases_and_repeated_refills_get_distinct_keys():
    engine = Engine()
    engine.accept(snapshot(42, 60))
    events = []
    for value in (43, 44, 45, 42, 45):
        events += recovered(engine.accept(snapshot(value, 60)))
    assert len(events) == 4
    assert len({e.key for e in events}) == 4


def test_missing_window_does_not_block_other_or_erase_baseline():
    engine = Engine()
    engine.accept(snapshot(20, 20))
    event, = recovered(engine.accept(snapshot(40, None)))
    assert "週間残量：未取得" in event.message
    event, = recovered(engine.accept(snapshot(None, 30)))
    assert event.event_type == "RATE_WEEKLY_RECOVERED"
    assert event.recovery["previous_remaining"] == 20
    assert recovered(engine.accept(snapshot(40, 30))) == []


def test_reset_time_change_without_increase_is_not_recovery():
    engine = Engine()
    engine.accept(snapshot(30, 30, reset=1))
    assert recovered(engine.accept(snapshot(30, 30, reset=9999999999))) == []


def test_settings_persist_reload_and_route_both_events(tmp_path):
    monitor = Monitor(tmp_path, mock=True)
    monitor.db = Database(tmp_path)
    try:
        for enabled in (True, False, True):
            monitor.handle("discord_enabled", enabled)
            monitor.config = load_config(tmp_path)
            assert monitor.config.discord_enabled == enabled
            engine = Engine()
            engine.accept(snapshot(25, 25))
            for e in recovered(engine.accept(snapshot(75, 75))):
                monitor.notify(e)
            assert monitor.notify_queues["DISCORD"].qsize() == (2 if enabled else 0)
            while not monitor.notify_queues["DISCORD"].empty():
                monitor.notify_queues["DISCORD"].get_nowait()
    finally:
        monitor.db.close()


def test_fetch_logs_and_stores_independent_recovery_details(tmp_path):
    monitor = Monitor(tmp_path, mock=True)
    monitor.db = Database(tmp_path)
    monitor.config = Config(discord_enabled=True)
    remaining = [22, 48]
    monitor.mock_payload = lambda: {"rateLimits": {
        "primary": {"usedPercent": 100-remaining[0], "windowDurationMins": 300, "resetsAt": 9999999999},
        "secondary": {"usedPercent": 100-remaining[1], "windowDurationMins": 10080, "resetsAt": 9999999999}}}
    try:
        monitor.fetch()
        remaining[:] = [73, 47]
        monitor.fetch()
        monitor.fetch()
        row, = monitor.db.conn.execute("SELECT event_type,previous_remaining,current_remaining,delta,five_hour_remaining,weekly_remaining FROM recoveries").fetchall()
        assert row == ("RATE_5H_RECOVERED", 22, 73, 51, 73, 47)
        comparisons = monitor.db.conn.execute("SELECT message FROM events WHERE kind='RATE_COMPARE'").fetchall()
        assert json.loads(comparisons[1][0])["delta_5h"] == 51
        assert json.loads(comparisons[1][0])["delta_weekly"] == -1
        assert monitor.notify_queues["WINDOWS"].qsize() == 1
        assert monitor.notify_queues["DISCORD"].qsize() == 1
        assert len(monitor.db.history("recoveries")[1]) == 1
    finally:
        monitor.db.close()
