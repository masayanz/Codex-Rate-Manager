"""GUIから独立した監視・永続化ワーカー。"""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from dataclasses import replace

from PySide6.QtCore import QObject, Signal

from .codex import AppServer
from .state import Engine, Event, Snapshot, RateWindow, State, classify, parse_rates, next_delay
from .storage import Config, Database, load_config, save_config, load_webhook, save_webhook, redact, accessible_config
from .notifications import DiscordClient, send_windows
from .startup import set_autostart


class MonitorSignals(QObject):
    update = Signal(object, str, str)
    config = Signal(object, bool)
    diagnostics = Signal(dict)
    history = Signal(str, object)
    result = Signal(str, bool, str)
    stopped = Signal()


class Monitor(threading.Thread):
    def __init__(self, data_dir: Path, mock: bool = False):
        super().__init__(name="rate-monitor", daemon=True)
        self.signals = MonitorSignals()
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.data_dir = data_dir
        self.mock = mock
        self.config = Config()
        self.webhook = ""
        self.db = None
        self.client = None
        self.snapshot = None
        self.engine = Engine()
        self.state = State.CONNECTING
        self.next_fetch = 0.0
        self.backoff = 30
        self.diag = {"接続状態": "接続待ち", "DB状態": "初期化待ち", "Discord状態": "未設定"}
        self.mock_state = "AVAILABLE"
        self.mock_reset = time.time() + 60
        self.reminder_seen = set()
        self.notify_queues = {channel: queue.Queue(maxsize=100) for channel in ("WINDOWS", "DISCORD")}
        self.log = logging.getLogger(f"codex_rate_manager.{id(self)}")

    def command(self, name, value=None):
        self.commands.put((name, value))

    def stop(self):
        self.stop_event.set()

    def event(self, kind, message=""):
        self.log.info("%s %s", kind, redact(message))
        if self.db:
            try:
                self.db.event(kind, str(redact(message)))
            except Exception:
                self.diag["DB状態"] = "履歴の保存に失敗しました"

    def publish(self, state, detail=""):
        self.state = state
        self.signals.update.emit(self.snapshot, state.value, detail)
        self.diag["接続状態"] = detail or state.value
        self.diag["App Server PID"] = getattr(self.client, "pid", None)
        self.diag["Codex CLI"] = getattr(self.client, "executable", "モック" if self.mock else "自動検出")
        self.diag["Codex version"] = getattr(self.client, "version", "—")
        self.signals.diagnostics.emit(dict(self.diag))

    def run(self):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            logs = self.data_dir / "logs"
            logs.mkdir(exist_ok=True)
            handler = RotatingFileHandler(logs / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.log.addHandler(handler)
            self.log.setLevel(logging.INFO)
            try:
                self.config = load_config(self.data_dir)
                self.webhook = load_webhook(self.data_dir)
            except Exception:
                self.signals.result.emit("設定", False, "設定またはWebhookを読み込めません。設定画面で再登録してください。")
            try:
                self.db = Database(self.data_dir)
                self.engine.restore_state(self.db.load_state())
                self.diag["DB状態"] = "正常"
            except Exception:
                self.diag["DB状態"] = "初期化失敗：保存先のアクセス権を確認してください"
            self.signals.config.emit(self.config, bool(self.webhook))
            self.event("APP_START", "mock" if self.mock else "live")
            for channel in self.notify_queues:
                threading.Thread(target=self.notification_loop, args=(channel,), daemon=True, name=channel).start()
            while not self.stop_event.is_set():
                try:
                    name, value = self.commands.get(timeout=0.2)
                    self.handle(name, value)
                except queue.Empty:
                    pass
                except Exception as exc:
                    self.event("ERROR", type(exc).__name__)
                    self.signals.result.emit("処理", False, "処理に失敗しました。診断画面と保存先を確認してください。")
                if self.stop_event.is_set():
                    break
                now = time.time()
                if now >= self.next_fetch:
                    self.fetch()
                if self.client:
                    if self.client.process and self.client.process.poll() is not None:
                        self.next_fetch = 0
                    # 更新通知は再取得のきっかけにする。復帰判定はreadの完全な応答で行う。
                    got_update = False
                    while True:
                        try:
                            self.client.updates.get_nowait()
                            got_update = True
                        except queue.Empty:
                            break
                    if got_update:
                        self.next_fetch = min(self.next_fetch, time.time() + 1)
                self.reminders()
        except Exception as exc:
            self.publish(State.ERROR, "初期化に失敗しました。保存先へのアクセス権を確認して再起動してください。")
            self.event("ERROR", type(exc).__name__)
        finally:
            if self.client:
                self.client.close()
            self.event("APP_STOP")
            if self.db:
                self.db.close()
            for handler in list(self.log.handlers):
                handler.close()
                self.log.removeHandler(handler)
            self.signals.stopped.emit()

    def handle(self, name, value):
        if name in ("refresh", "resume", "reconnect"):
            if name == "reconnect" and self.client:
                self.client.close()
                self.client = None
            self.next_fetch = 0
        elif name == "mock":
            if self.mock:
                self.mock_state = value
                self.next_fetch = 0
        elif name == "launch":
            from .codex import find_codex
            executable = self.config.codex_path or find_codex()
            if not executable:
                self.signals.result.emit("Codex", False, "Codex CLIが見つかりません。設定で実行ファイルを指定してください。")
            elif executable.lower().endswith(".exe"):
                subprocess.Popen([executable], creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                self.signals.result.emit("Codex", False, "Codex CLIをターミナルから起動するか、設定でcodex.exeを指定してください。")
        elif name == "history":
            if self.db:
                self.signals.history.emit(value, self.db.history(value))
        elif name == "save":
            config, webhook = value
            config = accessible_config(config)
            config.window = self.config.window
            try:
                if not self.mock and config.autostart != self.config.autostart:
                    set_autostart(config.autostart)
                if webhook is not None:
                    save_webhook(self.data_dir, webhook)
                save_config(self.data_dir, config)
                reconnect = config.codex_path != self.config.codex_path
                if config.tray_enabled != self.config.tray_enabled:
                    self.event("TRAY_ICON_SHOWN" if config.tray_enabled else "TRAY_ICON_HIDDEN")
                self.config = config
                if webhook is not None:
                    self.webhook = webhook
                self.signals.config.emit(config, bool(self.webhook))
                self.signals.result.emit("設定", True, "設定を保存しました。")
                if reconnect:
                    self.handle("reconnect", None)
            except Exception:
                self.signals.result.emit("設定", False, "設定の保存に失敗しました。Webhook形式と保存先のアクセス権を確認してください。")
        elif name == "tray_visibility":
            try:
                config = accessible_config(replace(self.config, tray_enabled=bool(value)))
                save_config(self.data_dir, config)
                if config.tray_enabled != self.config.tray_enabled:
                    self.event("TRAY_ICON_SHOWN" if config.tray_enabled else "TRAY_ICON_HIDDEN")
                self.config = config
                self.signals.config.emit(config, bool(self.webhook))
                message = "トレイ表示設定を保存しました。"
                if config.tray_enabled != bool(value):
                    message = "自動起動時の操作手段を確保するため、トレイ表示をONにしました。非表示にする場合は起動時のウィンドウ表示を有効にしてください。"
                self.signals.result.emit("トレイ表示", True, message)
            except Exception:
                self.signals.result.emit("トレイ表示", False, "トレイ表示設定を保存できません。保存先のアクセス権を確認してください。ウィンドウは開いたままにします。")
        elif name in ("window", "shutdown"):
            try:
                from .storage import valid_window
                if valid_window(value):
                    self.config.window = dict(value)
                    save_config(self.data_dir, self.config)
            finally:
                if name == "shutdown":
                    self.stop()
        elif name == "toggle":
            self.config.notifications_enabled = bool(value)
            save_config(self.data_dir, self.config)
            self.signals.config.emit(self.config, bool(self.webhook))
        elif name == "test":
            url = self.webhook if value is None else value
            event = Event("test", f"test:{time.time_ns()}", "Codex Rate Manager", "Discord通知テストです。\n正常に通知できています。")
            self.enqueue("DISCORD", event, url)
        elif name == "notification_result":
            channel, event, success, message = value
            self.diag[f"{channel}通知"] = message
            if channel == "DISCORD":
                self.diag["Discord状態"] = message
            if self.db:
                self.db.notification(event.kind, channel, "成功" if success else "失敗", event.message + "\n" + message)
            self.event(f"{channel}_NOTIFY_{'SUCCESS' if success else 'ERROR'}")
            self.signals.diagnostics.emit(dict(self.diag))
            if event.kind == "test":
                self.signals.result.emit("Discord通知テスト", success, "Discord通知テストに成功しました。" if success else "Discord通知に失敗しました。Webhook URLと通信環境を確認してください。")

    def fetch(self):
        try:
            if self.snapshot and any(w.reset_at and w.reset_at <= time.time() for w in (self.snapshot.five_hour, self.snapshot.weekly) if w):
                self.publish(State.VERIFYING, "リセット後の残量をCodexへ確認しています...")
            if self.mock:
                if self.mock_state == "DISCONNECTED":
                    raise ConnectionError("mock")
                payload = self.mock_payload()
            else:
                if not self.client:
                    self.publish(State.CONNECTING, "Codexへ接続しています...")
                    self.client = AppServer(self.config.codex_path)
                    self.client.start()
                    self.event("CODEX_CONNECTED")
                payload = self.client.read_rates()
            snapshot = parse_rates(payload)
            # A response without either required window is not a usable rate
            # snapshot.  Fail closed and enter the normal reconnect backoff;
            # otherwise a malformed response would be treated as a successful
            # fetch and the next retry could be delayed for several minutes.
            if snapshot.five_hour is None or snapshot.weekly is None:
                raise ValueError("required rate window is missing")
            self.snapshot = snapshot
            state = classify(snapshot, self.config.low_threshold)
            events = self.engine.accept(snapshot, self.config.low_threshold)
            self.diag["最終rate取得"] = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
            self.diag["5h window"] = str(snapshot.five_hour)
            self.diag["weekly window"] = str(snapshot.weekly)
            # 生の認証・アカウント情報は診断へ流さず、レート情報のみを採用。
            self.diag["最新レスポンス（レートのみ）"] = json.dumps(safe_rate_response(payload), ensure_ascii=False, indent=2)
            if self.db:
                try:
                    self.db.record_rate(snapshot, state, getattr(self.client, "version", "mock"))
                except Exception:
                    self.diag["DB状態"] = "レート履歴の保存に失敗しました"
            self.event("RATE_FETCH")
            self.publish(state, "レート枠を識別できません。診断画面を確認してください。" if state in (State.ERROR, State.DISCONNECTED) else "モックモード" if self.mock else "正常")
            for event in events:
                event_name = "RATE_LOW" if event.kind == "low" else "LIMIT_5H" if event.kind == "limit" and "5時間" in event.title else "LIMIT_WEEKLY" if event.kind == "limit" and "週間" in event.title else "LIMIT_OTHER" if event.kind == "limit" else "AVAILABLE"
                self.event(event_name, event.title)
                if event.kind == "reset":
                    if "5時間" in event.message.split("枠の上限解除")[0]:
                        self.event("RESET_5H")
                    if "週間" in event.message.split("枠の上限解除")[0]:
                        self.event("RESET_WEEKLY")
                self.notify(event)
            if self.db:
                try:
                    self.db.save_state(self.engine.export_state())
                except Exception:
                    self.diag["DB状態"] = "監視状態の保存に失敗しました"
            self.backoff = 30
            self.next_fetch = time.time() + next_delay(snapshot, time.time(), self.config.normal_interval, self.config.near_interval)
        except Exception as exc:
            if self.client:
                self.client.close()
                self.client = None
            self.event("CODEX_DISCONNECTED", type(exc).__name__)
            self.publish(State.DISCONNECTED, f"取得できません。CLIのパス・ログイン・通信を確認してください。{self.backoff}秒後に再接続します。")
            self.next_fetch = time.time() + self.backoff
            self.backoff = min(self.backoff * 2, 120)

    def mock_payload(self):
        used = {"AVAILABLE": (26, 59), "LOW": (85, 59), "LIMITED_5H": (100, 59), "LIMITED_WEEKLY": (15, 100), "RESET": (0, 0)}
        a, b = used.get(self.mock_state, (26, 59))
        reset = self.mock_reset if self.mock_state != "RESET" else time.time() + 18000
        return {"rateLimits": {"primary": {"usedPercent": a, "windowDurationMins": 300, "resetsAt": reset}, "secondary": {"usedPercent": b, "windowDurationMins": 10080, "resetsAt": reset + 86400}}}

    def reminders(self):
        if not self.snapshot or self.state in (State.DISCONNECTED, State.ERROR, State.CONNECTING):
            return
        for name, w in (("5時間", self.snapshot.five_hour), ("週間", self.snapshot.weekly)):
            if not w or not w.reset_at or w.remaining > 0:
                continue
            remaining = w.reset_at - time.time()
            for minutes in self.config.reminders:
                if 0 < remaining <= minutes * 60:
                    event = Event("reminder", f"reminder:{name}:{w.reset_at}:{minutes}", f"Codex {name}枠のリセット予定まで{minutes}分以内", "これは事前通知です。利用可能になったかは予定時刻後に確認します。")
                    if event.key not in self.reminder_seen:
                        self.reminder_seen.add(event.key)
                        self.notify(event)

    def notify(self, event):
        if not self.config.notifications_enabled:
            return
        for channel in self.notify_queues:
            prefix = channel.lower()
            enabled = (channel == "WINDOWS" or self.config.discord_enabled)
            enabled = enabled and (event.kind == "reminder" or getattr(self.config, f"{prefix}_{event.kind}", False))
            if enabled:
                self.enqueue(channel, event, self.webhook)

    def enqueue(self, channel, event, url):
        if not self.db:
            # 永続重複防止が使えないときは自動送信を抑止。手動テストは可能。
            if event.kind != "test":
                return
        else:
            try:
                if not self.db.claim_notification(event.key, channel):
                    return
            except Exception:
                self.diag["DB状態"] = "通知重複防止の保存に失敗したため送信を抑止しました"
                return
        try:
            self.notify_queues[channel].put_nowait((event, url))
        except queue.Full:
            self.event(f"{channel}_NOTIFY_ERROR", "通知キューが満杯です")

    def notification_loop(self, channel):
        while not self.stop_event.is_set():
            try:
                event, url = self.notify_queues[channel].get(timeout=0.3)
            except queue.Empty:
                continue
            jst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y/%m/%d %H:%M:%S JST")
            message = event.message + "\n確認時刻：" + jst
            try:
                result = DiscordClient().send(url, event.title, message, self.stop_event) if channel == "DISCORD" else send_windows(event.title, message)
            except Exception:
                result = (False, "通知を送信できませんでした")
            self.command("notification_result", (channel, event, *result))


def safe_rate_response(payload):
    """未知のフィールドを診断ログへ漏らさないallowlist。"""
    fields = {"result", "rateLimits", "rateLimitsByLimitId", "primary", "secondary", "usedPercent", "windowDurationMins", "resetsAt", "limitId", "limitName", "planType", "rate_limits", "window_duration_mins", "used_percent", "reset_at", "spendControlReached", "rateLimitReachedType", "individualLimit", "remainingPercent"}
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key, value in payload.items():
        if key not in fields:
            continue
        if key == "rateLimitsByLimitId" and isinstance(value, dict):
            result[key] = {str(k): safe_rate_response(v) for k, v in value.items() if str(k) == "codex"}
        else:
            result[key] = safe_rate_response(value) if isinstance(value, dict) else redact(value) if isinstance(value, (str, float, int, type(None))) else "[省略]"
    return result
