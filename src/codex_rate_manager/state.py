"""Immutable-ish rate data and state transitions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable
import math
import hashlib
import time
from datetime import datetime


class State(StrEnum):
    CONNECTING = "CONNECTING"
    AVAILABLE = "AVAILABLE"
    LOW = "LOW"
    LIMITED_5H = "LIMITED_5H"
    LIMITED_WEEKLY = "LIMITED_WEEKLY"
    LIMITED_OTHER = "LIMITED_OTHER"
    WAITING_RESET = "WAITING_RESET"
    VERIFYING = "VERIFYING"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class RateWindow:
    used_percent: float
    reset_at: float | None
    window_minutes: int

    @property
    def remaining(self) -> float:
        return max(0.0, min(100.0, 100.0 - self.used_percent))


@dataclass(frozen=True)
class Snapshot:
    five_hour: RateWindow | None
    weekly: RateWindow | None
    other: tuple[RateWindow, ...] = ()
    fetched_at: float = 0.0
    blocked_other: bool = False
    account_key: str | None = None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _window(item: Any) -> RateWindow | None:
    if not isinstance(item, dict):
        return None
    duration = _number(item.get("windowDurationMins", item.get("window_duration_mins")))
    used = _number(item.get("usedPercent", item.get("used_percent", item.get("used"))))
    if duration is None or used is None or duration <= 0 or not duration.is_integer() or not 0 <= used <= 100:
        return None
    reset = item.get("resetsAt", item.get("resetAt", item.get("reset_at")))
    reset_number = _number(reset)
    # App Server uses epoch seconds; tolerate epoch milliseconds.
    if reset_number is not None and reset_number > 100_000_000_000:
        reset_number /= 1000
    return RateWindow(max(0.0, min(100.0, used)), reset_number, int(duration))


def _items(payload: Any) -> Iterable[Any]:
    if not isinstance(payload, dict):
        return ()
    root = payload.get("result", payload)
    if isinstance(root, dict) and isinstance(root.get("rateLimitsByLimitId"), dict):
        buckets = root["rateLimitsByLimitId"]
        chosen = next((v for k, v in buckets.items() if str(k).lower() == "codex"), None)
        if chosen is None and any(isinstance(v, dict) and ("windowDurationMins" in v or "window_duration_mins" in v) for v in buckets.values()):
            return buckets.values()
        if chosen is None:
            if isinstance(root.get("rateLimits"), dict):
                return root["rateLimits"].values()
            chosen = buckets.get("primary") or buckets.get("default")
        if isinstance(chosen, dict) and isinstance(chosen.get("rateLimits"), dict):
            return chosen["rateLimits"].values()
        if isinstance(chosen, dict):
            return chosen.values() if any(isinstance(v, dict) for v in chosen.values()) else (chosen,)
    if isinstance(root, dict) and isinstance(root.get("rateLimits"), dict):
        return root["rateLimits"].values()
    if isinstance(root, dict) and isinstance(root.get("rate_limits"), dict):
        return root["rate_limits"].values()
    if isinstance(root, dict) and isinstance(root.get("limits"), list):
        return root["limits"]
    return ()


def parse_rates(payload: dict[str, Any]) -> Snapshot:
    """Parse an App Server rateLimits result, failing closed on absent windows."""
    windows = [w for item in _items(payload) if (w := _window(item)) is not None]
    five = next((w for w in windows if w.window_minutes == 300), None)
    weekly = next((w for w in windows if w.window_minutes == 10080), None)
    others = tuple(w for w in windows if w not in (five, weekly))
    root = payload.get("result", payload) if isinstance(payload, dict) else {}
    fetched = _number(root.get("fetchedAt")) if isinstance(root, dict) else None
    buckets = root.get("rateLimitsByLimitId") or {}
    selected = buckets.get("codex", root.get("rateLimits", {})) if isinstance(buckets, dict) else root.get("rateLimits", {})
    selected = selected if isinstance(selected, dict) else {}
    individual = selected.get("individualLimit")
    blocked = bool(selected.get("spendControlReached") or selected.get("rateLimitReachedType"))
    if isinstance(individual, dict):
        remaining = _number(individual.get("remainingPercent"))
        blocked = blocked or remaining is None or remaining <= 0
    account = root.get("accountId")
    account_key = hashlib.sha256(account.encode()).hexdigest() if isinstance(account, str) and account else None
    return Snapshot(five, weekly, others, fetched if fetched is not None else time.time(), blocked, account_key)


def classify(snapshot: Snapshot, threshold: float = 20) -> State:
    if snapshot.five_hour is None or snapshot.weekly is None:
        return State.DISCONNECTED
    if snapshot.five_hour.remaining <= 0:
        return State.LIMITED_5H
    if snapshot.weekly.remaining <= 0:
        return State.LIMITED_WEEKLY
    if snapshot.blocked_other or any(w.remaining <= 0 for w in snapshot.other):
        return State.LIMITED_OTHER
    if snapshot.five_hour.remaining <= threshold or snapshot.weekly.remaining <= threshold:
        return State.LOW
    return State.AVAILABLE


@dataclass(frozen=True)
class Event:
    kind: str
    key: str
    title: str
    message: str


class Engine:
    def __init__(self) -> None:
        self._previous: State | None = None
        self._limited = False
        self._notified_resets: set[str] = set()
        self._seen: set[str] = set()
        self._limited_windows: dict[str, float | None] = {}
        self._account_key = None

    def accept(self, snapshot: Snapshot, threshold: float = 20) -> list[Event]:
        if snapshot.account_key and self._account_key and snapshot.account_key != self._account_key:
            self.__init__()
        if snapshot.account_key:
            self._account_key = snapshot.account_key
        state = classify(snapshot, threshold)
        events: list[Event] = []
        windows = [("5時間", snapshot.five_hour), ("週間", snapshot.weekly)]
        windows += [(f"その他{w.window_minutes}分", w) for w in snapshot.other]
        # 必須枠が欠けた応答から上限解除を推測しない。
        if state != State.DISCONNECTED:
            for name, window in windows:
                if window.remaining > threshold:
                    continue
                kind = "limit" if window.remaining <= 0 else "low"
                if kind == "limit":
                    self._limited = True
                    self._limited_windows[name] = window.reset_at
                key = f"{kind}:{name}:{window.reset_at}"
                if key in self._seen:
                    continue
                self._seen.add(key)
                reset = _format_reset(window.reset_at)
                title = f"Codex {name}レート上限" if kind == "limit" else f"Codex {name}レート残り{window.remaining:g}%"
                events.append(Event(kind, key, title, f"現在の残量：{window.remaining:g}%\n次回リセット予定：{reset}\n{_remaining_text(snapshot)}"))
            if snapshot.blocked_other:
                self._limited = True
                self._limited_windows["その他の制限"] = None
                key = f"limit:other:{snapshot.five_hour.reset_at}:{snapshot.weekly.reset_at}"
                if key not in self._seen:
                    self._seen.add(key)
                    events.append(Event("limit", key, "Codex その他の制限に到達", "Codexが利用制限を報告しています。診断情報を確認してください。\n" + _remaining_text(snapshot)))
        if state in (State.AVAILABLE, State.LOW) and self._limited:
            # 上限を観測した古いepochを識別子とし、両枠のread確認後だけ復帰通知する。
            reset_key = "reset:" + ";".join(f"{k}:{v}" for k, v in sorted(self._limited_windows.items()))
            if reset_key not in self._notified_resets:
                self._notified_resets.add(reset_key)
                names = "・".join(self._limited_windows)
                events.append(Event("reset", reset_key, "Codexが利用可能になりました", f"{names}枠の上限解除を確認しました。\n{_remaining_text(snapshot)}\nCodex作業を再開できます。"))
            self._limited = False
            self._limited_windows.clear()
        self._previous = state
        return events

    def export_state(self) -> dict[str, Any]:
        return {"previous": self._previous.value if self._previous else None, "limited": self._limited, "limited_windows": self._limited_windows, "notified_resets": sorted(self._notified_resets)[-1000:], "seen": sorted(self._seen)[-2000:], "account_key": self._account_key}

    def restore_state(self, data: dict[str, Any]) -> None:
        try:
            self._previous = State(data["previous"]) if data.get("previous") else None
        except (ValueError, TypeError):
            self._previous = None
        self._limited = bool(data.get("limited", False))
        self._limited_windows = dict(data.get("limited_windows", {}))
        self._notified_resets = set(str(v) for v in data.get("notified_resets", ()))
        self._seen = set(str(v) for v in data.get("seen", ()))
        self._account_key = data.get("account_key")


def _format_reset(epoch):
    try:
        return datetime.fromtimestamp(epoch).strftime("%Y/%m/%d %H:%M:%S") if epoch else "不明"
    except (ValueError, OSError, OverflowError):
        return "不明"


def _remaining_text(snapshot):
    return f"5時間残量：{snapshot.five_hour.remaining:g}%\n週間残量：{snapshot.weekly.remaining:g}%"


def next_delay(snapshot: Snapshot | None, now: float | None = None, normal: float = 300, near: float = 60) -> float:
    now = time.time() if now is None else now
    if snapshot is None or snapshot.five_hour is None or snapshot.weekly is None:
        return normal
    resets = [w.reset_at for w in (snapshot.five_hour, snapshot.weekly) if w.reset_at is not None]
    remaining = min(resets) - now if resets else float("inf")
    # A stale reset timestamp must never create a tight polling loop.
    if remaining <= 0:
        return min(normal, 30)
    if remaining <= 60:
        return max(0.05, min(normal, 30, remaining))
    if remaining <= 600:
        return min(normal, near)
    return normal
