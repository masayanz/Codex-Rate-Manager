"""実Monitor・通知ワーカーを通す、独立データでの復帰検証。"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from .monitor import Monitor
from .storage import Config, Database, load_webhook, save_config


class VerificationMonitor(Monitor):
    def __init__(self, data_dir):
        super().__init__(data_dir, mock=True)
        self.next_fetch = float("inf")
        self.remaining = (100, 50)
        self.results = []
        self.emitted = []
        self.recovery_events = []

    def event(self, kind, message=""):
        self.emitted.append((kind, message))
        super().event(kind, message)

    def handle(self, name, value):
        if name == "verification":
            action, done, errors = value
            try:
                action(self)
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                done.set()
            return
        super().handle(name, value)
        if name == "notification_result":
            self.results.append(value)

    def fetch(self):
        super().fetch()
        self.next_fetch = float("inf")

    def mock_payload(self):
        five, weekly = self.remaining
        return {"rateLimits": {
            "primary": {"usedPercent": 100-five, "windowDurationMins": 300, "resetsAt": self.mock_reset},
            "secondary": {"usedPercent": 100-weekly, "windowDurationMins": 10080, "resetsAt": self.mock_reset+86400},
        }}

    def notify(self, event, url=None):
        event = replace(event, title="【Mock復帰検証】" + event.title,
                        message="検証用のレート情報による通知です。\n" + event.message)
        if event.event_type.endswith("RECOVERED"):
            self.recovery_events.append(event)
        super().notify(event, url)


def _wait(predicate, seconds=90):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("verification timed out")
        time.sleep(.05)


def _call(monitor, action):
    done = threading.Event()
    errors = []
    monitor.command("verification", (action, done, errors))
    if not done.wait(10) or errors:
        raise RuntimeError("monitor command failed: " + ",".join(errors))


def run_recovery_verification(data_dir: Path, *, live_notifications=False):
    # 新規フォルダーだけを使い、本番設定・DBを変更しない。
    data_dir = data_dir.resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError("検証には空の --data-dir を指定してください。")
    data_dir.mkdir(parents=True, exist_ok=True)
    report = {"success": False, "mode": "live" if live_notifications else "mock"}
    monitor = None
    try:
        if live_notifications:
            root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexRateManager"
            webhook = load_webhook(root)
            if not webhook:
                raise ValueError("保存済みWebhookを読み込めません。")
        else:
            webhook = "https://discord.com/api/webhooks/123456789012345/verification-token"
        config = Config(discord_enabled=True, windows_low=False, windows_limit=False,
                        discord_low=False, discord_limit=False, reminders=[])
        save_config(data_dir, config)
        monitor = VerificationMonitor(data_dir)
        with ExitStack() as stack:
            if not live_notifications:
                stack.enter_context(patch("codex_rate_manager.notifications.requests.post",
                                          return_value=SimpleNamespace(status_code=204)))
                stack.enter_context(patch("codex_rate_manager.monitor.send_windows",
                                          return_value=(True, "Mock Windows SUCCESS")))
            monitor.start()
            _call(monitor, lambda m: setattr(m, "webhook", webhook))
            _call(monitor, lambda m: m.fetch())
            initial_clean = not monitor.recovery_events and not monitor.results
            _call(monitor, lambda m: m.handle("test", None))
            _wait(lambda: len(monitor.results) == 1)

            def limited(m):
                m.remaining = (0, 50)
                m.mock_reset = time.time() + .5
                m.fetch()
            _call(monitor, limited)
            _wait(lambda: time.time() > monitor.mock_reset, 5)

            def recover(m):
                m.remaining = (100, 50)
                m.mock_reset = time.time() + 18000
                m.fetch()  # reminders -> WAITING_RESET -> VERIFYING -> AVAILABLE
            _call(monitor, recover)
            _wait(lambda: len(monitor.results) == 3)
            recovery = monitor.recovery_events[0]
            _call(monitor, lambda m: Monitor.notify(m, recovery))
            _call(monitor, lambda m: m.fetch())
            recovery_count = len(monitor.recovery_events)

            def weekly_limited(m):
                m.remaining = (0, 0)
                m.mock_reset = time.time() + 120
                m.fetch()
                m.remaining = (100, 0)
                m.fetch()
            _call(monitor, weekly_limited)
            partial = monitor.recovery_events[-1]
            weekly_safe = partial.kind == "reset" and monitor.state.value == "LIMITED_WEEKLY"
            _wait(lambda: len(monitor.results) == 5)
            monitor.stop()
            monitor.join(5)
            if monitor.is_alive():
                raise TimeoutError("monitor stop timed out")

        db = Database(data_dir)
        try:
            rows = db.conn.execute("SELECT kind,channel,result,message FROM notifications ORDER BY id").fetchall()
        finally:
            db.close()
        recoveries = [r for r in rows if r[0] == "RATE_5H_RECOVERED"]
        tests = [r for r in rows if r[0] == "test"]
        transitions = [message for kind, message in monitor.emitted if kind == "STATE_TRANSITION"]
        expected = ["previous_state=LIMITED_5H current_state=WAITING_RESET",
                    "previous_state=WAITING_RESET current_state=VERIFYING",
                    "previous_state=VERIFYING current_state=AVAILABLE"]
        counts = {channel: sum(r[1] == channel for r in recoveries) for channel in ("WINDOWS", "DISCORD")}
        duplicate_count = sum("reason=duplicate" in message for kind, message in monitor.emitted)
        status = [int(s) for r in rows if r[1] == "DISCORD" for s in re.findall(r"Discord HTTP (\d+)", r[3])]
        report.update(initial_available_no_recovery=initial_clean,
                      state_transitions=transitions, recovery_event_count=recovery_count,
                      notification_counts=counts, test_notification_count=len(tests),
                      duplicate_suppressed=duplicate_count == 2,
                      weekly_zero_no_resume=weekly_safe,
                      discord_http_status=status,
                      notification_history=[{"event_type": r[0], "channel": r[1], "status": "SUCCESS" if r[2] else "FAILED"} for r in rows])
        report["success"] = (initial_clean and recovery_count == 1 and counts == {"WINDOWS": 2, "DISCORD": 2}
                             and len(tests) == 1 and len(rows) == 5 and all(r[2] for r in rows)
                             and duplicate_count == 2 and weekly_safe and all(t in transitions for t in expected)
                             and len(status) == 3 and all(200 <= s < 300 for s in status))
    except Exception as exc:
        report["error"] = type(exc).__name__
    finally:
        if monitor and monitor.is_alive():
            monitor.stop()
            monitor.join(5)
        (data_dir / "recovery-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
