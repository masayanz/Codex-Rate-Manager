"""残量増加の必須ケースを、実ワーカー・DB・通知経路で検証（外部送信なし）。"""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .recovery_verification import VerificationMonitor, _call, _wait
from .state import Engine
from .storage import Config, Database, save_config


CASES = [
    ((0, 50), (100, 49), ["RATE_5H_RECOVERED"]),
    ((25, 50), (75, 49), ["RATE_5H_RECOVERED"]),
    ((50, 20), (49, 70), ["RATE_WEEKLY_RECOVERED"]),
    ((10, 20), (100, 100), ["RATE_5H_RECOVERED", "RATE_WEEKLY_RECOVERED"]),
    ((80, 70), (79, 69), []),
    ((42, 60), (43, 60), ["RATE_5H_RECOVERED"]),
    (None, (100, 100), []),
    ((34, 29), (100, 100), ["RATE_5H_RECOVERED", "RATE_WEEKLY_RECOVERED"]),
    ((22, 48), (73, 47), ["RATE_5H_RECOVERED"]),
]


def run_increase_verification(data_dir):
    data_dir = Path(data_dir).resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError("検証には空のフォルダーを指定してください。")
    data_dir.mkdir(parents=True, exist_ok=True)
    save_config(data_dir, Config(discord_enabled=True, windows_low=False, windows_limit=False,
                                 discord_low=False, discord_limit=False, reminders=[]))
    monitor = VerificationMonitor(data_dir)
    report = {"success": False, "mode": "mock", "cases": []}
    try:
        with patch("codex_rate_manager.notifications.requests.post", return_value=SimpleNamespace(status_code=204)), \
             patch("codex_rate_manager.monitor.send_windows", return_value=(True, "Mock Windows SUCCESS")):
            monitor.start()
            def setup(m):
                m.webhook = "https://discord.com/api/webhooks/123456789012345/verification-token"
                m.mock_reset = 9999999999  # 全ケース、予定時刻より前の通常ポーリング。
                m.handle("test", None)
            _call(monitor, setup)
            _wait(lambda: len(monitor.results) == 1)
            expected_results = 1
            for index, (previous, current, expected) in enumerate(CASES, 1):
                start = len(monitor.recovery_events)
                def poll(m):
                    m.engine = Engine()
                    if previous is not None:
                        m.remaining = previous
                        m.fetch()
                    m.remaining = current
                    m.fetch()
                    m.fetch()  # 同値の再取得で重複イベントが生じないこと。
                _call(monitor, poll)
                events = monitor.recovery_events[start:]
                actual = [e.event_type for e in events]
                assert actual == expected, (index, actual)
                expected_results += 2 * len(expected)
                _wait(lambda: len(monitor.results) >= expected_results)
                assert len(monitor.results) == expected_results
                report["cases"].append({"case": index, "previous": previous, "current": current,
                                        "event_types": actual, "passed": True})
            _call(monitor, _notify_duplicate)
            monitor.stop()
            monitor.join(5)
            assert not monitor.is_alive()
        db = Database(data_dir)
        try:
            stored = db.conn.execute("SELECT event_type,previous_remaining,current_remaining,delta,five_hour_remaining,weekly_remaining FROM recoveries ORDER BY rowid").fetchall()
            rows = db.conn.execute("SELECT kind,channel,result FROM notifications ORDER BY id").fetchall()
        finally:
            db.close()
        counts = {channel: sum(r[1] == channel and r[0] != "test" for r in rows) for channel in ("WINDOWS", "DISCORD")}
        report.update(recovery_count=len(stored), notification_counts=counts,
                      notification_history=[{"event_type": r[0], "channel": r[1], "success": bool(r[2])} for r in rows],
                      recovery_history=stored,
                      events=[asdict(e) for e in monitor.recovery_events])
        report["success"] = len(stored) == 9 and counts == {"WINDOWS": 9, "DISCORD": 9} and len(rows) == 19 and all(r[2] for r in rows)
    except Exception as exc:
        report["error"] = type(exc).__name__
    finally:
        if monitor.is_alive():
            monitor.stop()
            monitor.join(5)
        (data_dir / "increase-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _notify_duplicate(monitor):
    from .monitor import Monitor
    Monitor.notify(monitor, monitor.recovery_events[-1])
