"""回復イベントの状態遷移と通知判定に関する回帰テスト。"""

from dataclasses import replace
import queue

from codex_rate_manager.state import Engine, Event, RateWindow, Snapshot
from codex_rate_manager.monitor import Monitor
from codex_rate_manager.notifications import NotificationManager
from codex_rate_manager.storage import Config, Database


def snapshot(five_remaining, weekly_remaining, five_reset=1_000.0, weekly_reset=6_000.0):
    return Snapshot(
        RateWindow(100 - five_remaining, five_reset, 300),
        RateWindow(100 - weekly_remaining, weekly_reset, 10080),
    )


def recovery_events(events):
    return [event for event in events if event.event_type.endswith("_RECOVERED")]


def test_five_hour_recovery_emits_dedicated_event_once_without_epoch_key():
    engine = Engine()

    assert engine.accept(snapshot(0, 50, five_reset=1_000.0))
    events = engine.accept(snapshot(100, 50, five_reset=2_000.0))
    recovered = recovery_events(events)

    assert len(recovered) == 1
    assert recovered[0].event_type == "RATE_5H_RECOVERED"
    assert recovered[0].kind == "reset"
    assert recovered[0].recovery["delta"] == 100
    assert recovery_events(engine.accept(snapshot(100, 50, five_reset=2_000.0))) == []


def test_five_hour_recovery_from_limited_to_low_still_emits_reset_event():
    """週間枠が残り少なくても、5時間枠の実復帰は失わない。"""
    engine = Engine()
    engine.accept(snapshot(0, 16))

    recovered = recovery_events(engine.accept(snapshot(100, 16)))

    assert len(recovered) == 1
    assert recovered[0].event_type == "RATE_5H_RECOVERED"
    assert recovered[0].kind == "reset"


def test_five_hour_partial_recovery_notifies_without_offering_resume():
    engine = Engine()
    engine.accept(snapshot(0, 50))

    events = engine.accept(snapshot(100, 0))
    recovered = recovery_events(events)

    assert len(recovered) == 1
    assert recovered[0].event_type == "RATE_5H_RECOVERED"
    assert recovered[0].kind == "reset"
    assert "作業を再開できます" not in recovered[0].message


def test_weekly_recovery_is_separate_event_and_full_recovery_is_resumable():
    engine = Engine()
    engine.accept(snapshot(50, 0, weekly_reset=7_000.0))

    events = engine.accept(snapshot(50, 100, weekly_reset=8_000.0))
    recovered = recovery_events(events)

    assert len(recovered) == 1
    assert recovered[0].event_type == "RATE_WEEKLY_RECOVERED"
    assert recovered[0].kind == "reset"


def test_both_windows_recover_as_two_independent_notifications():
    engine = Engine()
    engine.accept(snapshot(0, 0, five_reset=1_000.0, weekly_reset=6_000.0))

    recovered = recovery_events(engine.accept(snapshot(100, 100, five_reset=2_000.0, weekly_reset=8_000.0)))

    assert {event.event_type for event in recovered} == {
        "RATE_5H_RECOVERED",
        "RATE_WEEKLY_RECOVERED",
    }
    assert sum(event.kind == "reset" for event in recovered) == 2
    assert sum(event.kind == "internal" for event in recovered) == 0


def test_initial_available_does_not_emit_recovery_event():
    assert recovery_events(Engine().accept(snapshot(100, 50))) == []


def test_limit_epoch_jitter_and_restart_establish_new_baseline():
    engine = Engine()
    engine.accept(snapshot(0, 50, five_reset=1000.0))
    assert engine.accept(snapshot(0, 50, five_reset=1001.0)) == []
    restored = Engine()
    restored.restore_state(engine.export_state())
    assert recovery_events(restored.accept(snapshot(100, 50, five_reset=2000.0))) == []


def test_partial_recovery_survives_restart_without_repeating_five_hour_event():
    engine = Engine()
    engine.accept(snapshot(0, 0))
    assert len(recovery_events(engine.accept(snapshot(100, 0)))) == 1
    restored = Engine()
    restored.restore_state(engine.export_state())
    assert recovery_events(restored.accept(snapshot(100, 0))) == []
    event, = recovery_events(restored.accept(snapshot(100, 50)))
    assert event.event_type == "RATE_WEEKLY_RECOVERED"
    assert event.kind == "reset"


def test_account_change_cannot_recover_previous_account():
    engine = Engine()
    engine.accept(replace(snapshot(0, 50), account_key="old"))

    assert recovery_events(engine.accept(replace(snapshot(100, 50), account_key="new"))) == []


def test_notification_manager_manual_test_ignores_disabled_automatic_settings():
    queued = []
    logs = []
    config = Config(notifications_enabled=False, discord_enabled=False, discord_reset=False)
    event = Event("test", "test:1", "test", "test message")

    NotificationManager(lambda *args: queued.append(args), lambda *args: logs.append(args)).send(event, config, "url")

    assert [channel for channel, *_ in queued] == ["DISCORD"]
    assert any("reason=manual_test" in message for _, message in logs)


def test_monitor_routes_one_reset_key_to_each_enabled_channel_once(tmp_path):
    monitor = Monitor(tmp_path)
    monitor.db = Database(tmp_path)
    monitor.config = Config(notifications_enabled=True, discord_enabled=True, discord_reset=True)
    event = Event("reset", "RATE_5H_RECOVERED:1000.0", "recovered", "resume", "RATE_5H_RECOVERED")

    monitor.notify(event, "url")
    monitor.notify(event, "url")

    assert monitor.notify_queues["WINDOWS"].qsize() == 1
    assert monitor.notify_queues["DISCORD"].qsize() == 1
    monitor.db.close()


def test_queue_full_releases_claim_for_later_delivery(tmp_path):
    monitor = Monitor(tmp_path)
    monitor.db = Database(tmp_path)
    monitor.notify_queues["DISCORD"] = queue.Queue(maxsize=1)
    monitor.notify_queues["DISCORD"].put(("occupied", "url"))
    event = Event("reset", "RATE_5H_RECOVERED:2000.0", "recovered", "resume", "RATE_5H_RECOVERED")

    monitor.enqueue("DISCORD", event, "url")

    assert monitor.db.claim_notification(event.key, "DISCORD")
    monitor.db.close()


def test_failed_delivery_releases_claim_for_retry(tmp_path):
    monitor = Monitor(tmp_path)
    monitor.db = Database(tmp_path)
    event = Event("reset", "RATE_5H_RECOVERED:3000.0", "recovered", "resume", "RATE_5H_RECOVERED")
    assert monitor.db.claim_notification(event.key, "DISCORD")

    monitor.handle("notification_result", ("DISCORD", event, False, "HTTP 500"))

    assert monitor.db.claim_notification(event.key, "DISCORD")
    monitor.db.close()


def test_weekly_recovery_routes_discord_while_five_hour_is_zero(tmp_path):
    monitor = Monitor(tmp_path)
    monitor.db = Database(tmp_path)
    monitor.config = Config(discord_enabled=True, discord_low=False, discord_limit=False)
    monitor.engine.accept(snapshot(0, 0))
    events = recovery_events(monitor.engine.accept(snapshot(0, 100)))
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "RATE_WEEKLY_RECOVERED"
    assert "5時間残量：0%" in event.message
    assert "週間残量：100%" in event.message
    assert "5時間リセット：" in event.message and "週間リセット：" in event.message
    monitor.notify(event)
    assert monitor.notify_queues["DISCORD"].qsize() == 1
    monitor.db.close()


def test_previous_limited_state_does_not_replace_first_baseline():
    for state, event_type in (("LIMITED_5H", "RATE_5H_RECOVERED"), ("LIMITED_WEEKLY", "RATE_WEEKLY_RECOVERED")):
        engine = Engine()
        engine.restore_state({"previous": state})
        events = recovery_events(engine.accept(snapshot(100, 100)))
        assert events == []
        assert recovery_events(engine.accept(snapshot(100, 100))) == []


def test_mock_notification_workers_deliver_independent_recovery(tmp_path):
    from codex_rate_manager.recovery_verification import run_recovery_verification
    report = run_recovery_verification(tmp_path)
    assert report["success"], report
