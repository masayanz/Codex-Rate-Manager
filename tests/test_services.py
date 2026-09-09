import json
import os
from types import SimpleNamespace

from codex_rate_manager.notifications import DiscordClient, validate_webhook
from codex_rate_manager.storage import Config, Database, load_config, save_config


def test_config_invalid_values_use_defaults(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"normal_interval": -1, "reminders": [20, "bad"]}))
    config = load_config(tmp_path)
    assert config.normal_interval == Config().normal_interval
    assert config.reminders == [10]


def test_legacy_config_migration_preserves_settings(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"normal_interval": 120, "discord_enabled": True, "future_field": 7}))
    config = load_config(tmp_path)
    assert config.skin_id == "neon_future" and config.normal_interval == 120
    assert config.discord_enabled
    save_config(tmp_path, config)
    raw = json.loads((tmp_path / "config.json").read_text())
    assert raw["future_field"] == 7
    assert raw["skin_id"] == "neon_future"


def test_ico_contains_all_windows_sizes():
    import struct
    from codex_rate_manager.resources import resource_path
    data = resource_path("assets/app.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind, count) == (0, 1, 9)
    assert [data[6 + i * 16] or 256 for i in range(count)] == [16, 20, 24, 32, 40, 48, 64, 128, 256]


def test_database_dedup_survives_reopen(tmp_path):
    db = Database(tmp_path)
    assert db.claim_notification("reset", "windows")
    db.close()
    db = Database(tmp_path)
    assert not db.claim_notification("reset", "windows")
    db.save_state({"limited": True})
    assert db.load_state() == {"limited": True}
    db.close()


def test_history_is_human_readable(tmp_path):
    from codex_rate_manager.state import RateWindow, Snapshot
    db = Database(tmp_path)
    db.record_rate(Snapshot(RateWindow(25, 2000000000, 300), RateWindow(50, 2000001000, 10080)), "LOW", "v1")
    headers, rows = db.history()
    assert headers[0] == "日時"
    assert rows[0][1] == "25.0%"
    assert rows[0][2] == "75.0%"
    assert "/" in rows[0][0]
    db.notification("low", "discord", "失敗", "通信エラー")
    nheaders, nrows = db.history("notifications")
    assert nheaders == ["日時", "種類", "チャンネル", "結果", "メッセージ"]
    assert nrows[0][3] == "失敗"
    db.close()


def test_webhook_validation():
    assert validate_webhook("https://discord.com/api/webhooks/123456789012345/token")
    assert not validate_webhook("https://discord.com/api/webhooks/id/token")
    assert not validate_webhook("https://discord.com/api/webhooks/123456789012345/token?x=1")


def test_discord_send_uses_expected_payload(monkeypatch):
    calls = []
    monkeypatch.setattr("codex_rate_manager.notifications.requests.post", lambda *a, **kw: (calls.append(kw) or SimpleNamespace(status_code=204)))
    assert DiscordClient().send("https://discord.com/api/webhooks/123456789012345/token", "Title", "Body")[0]
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["json"]["username"] == "Codex Rate Manager"


def test_discord_204_and_400(monkeypatch):
    responses = iter([SimpleNamespace(status_code=204), SimpleNamespace(status_code=400)])
    calls = []
    monkeypatch.setattr("codex_rate_manager.notifications.requests.post", lambda *a, **kw: (calls.append(1) or next(responses)))
    client = DiscordClient()
    assert client.send("https://discord.com/api/webhooks/123456789012345/token", "t", "m")[0]
    assert not client.send("https://discord.com/api/webhooks/123456789012345/token", "t", "m")[0]
    assert len(calls) == 2


def test_discord_429_header_waits_exactly(monkeypatch):
    responses = iter([SimpleNamespace(status_code=429, headers={"Retry-After": "61"}), SimpleNamespace(status_code=204)])
    waits = []
    class Stop:
        def wait(self, seconds): waits.append(seconds); return False
    monkeypatch.setattr("codex_rate_manager.notifications.requests.post", lambda *a, **kw: next(responses))
    assert DiscordClient().send("https://discord.com/api/webhooks/123456789012345/token", "t", "m", Stop())[0]
    assert waits == [61.0]


def test_discord_429_body_retry_after(monkeypatch):
    responses = iter([SimpleNamespace(status_code=429, headers={}, json=lambda: {"retry_after": 2.5}), SimpleNamespace(status_code=204)])
    waits = []
    class Stop:
        def wait(self, seconds): waits.append(seconds); return False
    monkeypatch.setattr("codex_rate_manager.notifications.requests.post", lambda *a, **kw: next(responses))
    assert DiscordClient().send("https://discord.com/api/webhooks/123456789012345/token", "t", "m", Stop())[0]
    assert waits == [2.5]


def test_discord_500_retries_three_times(monkeypatch):
    calls = []
    monkeypatch.setattr("codex_rate_manager.notifications.requests.post", lambda *a, **kw: (calls.append(1) or SimpleNamespace(status_code=500)))
    monkeypatch.setattr("codex_rate_manager.notifications.time.sleep", lambda seconds: calls.append(seconds))
    assert not DiscordClient().send("https://discord.com/api/webhooks/123456789012345/token", "t", "m")[0]
    assert calls == [1, 5, 1, 15, 1]


def test_discord_connection_error_retries_three_times(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise __import__("requests").RequestException("offline")
    monkeypatch.setattr("codex_rate_manager.notifications.requests.post", fail)
    monkeypatch.setattr("codex_rate_manager.notifications.time.sleep", lambda seconds: None)
    assert not DiscordClient().send("https://discord.com/api/webhooks/123456789012345/token", "t", "m")[0]
    assert len(calls) == 3


def test_discord_cancellation_during_wait(monkeypatch):
    class Stop:
        def wait(self, seconds): return True
    monkeypatch.setattr("codex_rate_manager.notifications.requests.post", lambda *a, **kw: SimpleNamespace(status_code=500))
    assert DiscordClient().send("https://discord.com/api/webhooks/123456789012345/token", "t", "m", Stop()) == (False, "cancelled")


def test_dpapi_roundtrip_on_windows(tmp_path):
    if os.name != "nt":
        import pytest
        pytest.skip("DPAPI is Windows-only")
    from codex_rate_manager.storage import load_webhook, save_webhook
    url = "https://discord.com/api/webhooks/123456789012345/secret-token"
    save_webhook(tmp_path, url)
    assert load_webhook(tmp_path) == url
    assert url.encode() not in (tmp_path / "webhook.bin").read_bytes()
