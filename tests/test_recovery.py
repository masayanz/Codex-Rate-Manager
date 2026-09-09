from dataclasses import replace

import pytest

from codex_rate_manager.state import Engine, RateWindow, Snapshot, State, classify, next_delay, parse_rates
from codex_rate_manager.monitor import safe_rate_response


def snapshot(five=0, weekly=0, reset=1000, fetched=100):
    return Snapshot(RateWindow(five, reset, 300), RateWindow(weekly, reset + 5000, 10080), fetched_at=fetched)


@pytest.mark.parametrize("used,remaining", [(0, 100), (75, 25), (100, 0)])
def test_remaining(used, remaining):
    assert RateWindow(used, None, 300).remaining == remaining


@pytest.mark.parametrize("a,b,expected", [(0, 0, State.AVAILABLE), (85, 0, State.LOW), (100, 0, State.LIMITED_5H), (0, 100, State.LIMITED_WEEKLY)])
def test_states(a, b, expected):
    assert classify(snapshot(a, b)) == expected


def test_five_reset_cannot_clear_weekly_limit():
    engine = Engine()
    engine.accept(snapshot(100, 100))
    assert not any(e.kind == "reset" for e in engine.accept(snapshot(0, 100)))
    events = engine.accept(snapshot(0, 85))
    resets = [e for e in events if e.kind == "reset"]
    assert len(resets) == 1
    assert "週間残量：15%" in resets[0].message
    assert not any(e.kind == "reset" for e in engine.accept(snapshot(0, 0)))


def test_deadline_and_missing_window_cannot_trigger_recovery():
    engine = Engine()
    engine.accept(snapshot(100, 0))
    assert not any(e.kind == "reset" for e in engine.accept(snapshot(100, 0, fetched=2000)))
    engine.accept(Snapshot(RateWindow(0, 10000, 300), None))
    restored = Engine()
    restored.restore_state(engine.export_state())
    assert len([e for e in restored.accept(snapshot()) if e.kind == "reset"]) == 1


def test_low_alert_once_per_window_not_per_poll():
    engine = Engine()
    assert len(engine.accept(snapshot(85))) == 1
    assert engine.accept(snapshot(86, fetched=200)) == []
    assert engine.accept(snapshot(0)) == []
    assert engine.accept(snapshot(85)) == []
    assert len(engine.accept(snapshot(85, reset=2000))) == 1


def test_initial_available_never_notifies_reset():
    assert Engine().accept(snapshot()) == []


def test_changed_account_does_not_recover_old_account():
    engine = Engine()
    engine.accept(replace(snapshot(100), account_key="old"))
    assert engine.accept(replace(snapshot(), account_key="new")) == []


def test_parser_durations_override_primary_secondary_and_codex_bucket_wins():
    result = parse_rates({"rateLimitsByLimitId": {
        "codex-special-model": {"primary": {"usedPercent": 100, "windowDurationMins": 300}},
        "codex": {"primary": {"usedPercent": 20, "windowDurationMins": 10080}, "secondary": {"usedPercent": 0, "windowDurationMins": 300}},
    }})
    assert result.five_hour.remaining == 100
    assert result.weekly.remaining == 80


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 101, True])
def test_bad_used_values_fail_closed(bad):
    result = parse_rates({"rateLimits": {"primary": {"usedPercent": bad, "windowDurationMins": 300}}})
    assert result.five_hour is None


def test_backend_other_limits_block_recovery():
    engine = Engine()
    engine.accept(snapshot(100))
    assert classify(replace(snapshot(), blocked_other=True)) == State.LIMITED_OTHER
    assert not any(e.kind == "reset" for e in engine.accept(replace(snapshot(), blocked_other=True)))


@pytest.mark.parametrize("field,value", [("spendControlReached", True), ("rateLimitReachedType", "workspace_owner_usage_limit_reached"), ("individualLimit", {"remainingPercent": 0})])
def test_parser_reports_additional_limits(field, value):
    payload = {"rateLimits": {"primary": {"usedPercent": 0, "windowDurationMins": 300}, "secondary": {"usedPercent": 0, "windowDurationMins": 10080}, field: value}}
    assert classify(parse_rates(payload)) == State.LIMITED_OTHER


@pytest.mark.parametrize("remaining,expected", [(1000, 300), (600, 60), (60, 30), (12, 12), (0, 30), (-500, 30)])
def test_schedule(remaining, expected):
    assert next_delay(snapshot(reset=100 + remaining), now=100) == expected


def test_diagnostic_allowlist_excludes_identity_and_secrets():
    data = safe_rate_response({"accountId": "private-id", "cookie": "private-cookie", "rateLimitResetCredits": {"id": "private"}, "rateLimits": {"primary": {"usedPercent": 5, "token": "secret"}}})
    assert data == {"rateLimits": {"primary": {"usedPercent": 5}}}
