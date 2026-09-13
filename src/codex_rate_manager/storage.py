"""Persistent configuration, secrets, and history storage."""
from __future__ import annotations
import json, os, re, sqlite3, ctypes, time
from datetime import datetime
from dataclasses import dataclass, asdict, field, replace
from pathlib import Path
from typing import Any

@dataclass
class Config:
    normal_interval: int = 300
    near_interval: int = 60
    low_threshold: int = 20
    windows_low: bool = True
    windows_limit: bool = True
    windows_reset: bool = True
    discord_enabled: bool = False
    discord_low: bool = True
    discord_limit: bool = True
    discord_reset: bool = True
    reminders: list[int] = field(default_factory=lambda: [10])
    notifications_enabled: bool = True
    autostart: bool = False
    show_on_start: bool = True
    tray_enabled: bool = True
    codex_path: str = ""
    tray_style: str = "rings"
    skin_id: str = "neon_future"
    glow_enabled: bool = True
    animation_enabled: bool = True
    window: dict = field(default_factory=dict)
    display_mode: str = "standard"
    window_positions: dict = field(default_factory=dict)

def load_config(data_dir: Path) -> Config:
    path = Path(data_dir) / "config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return Config()
    if not isinstance(raw, dict): return Config()
    fields = set(Config.__dataclass_fields__)
    values = {k:v for k,v in raw.items() if k in fields}
    defaults = asdict(Config())
    for key, value in values.items():
        default = defaults[key]
        valid = type(value) is type(default)
        if key == "normal_interval": valid = type(value) is int and 30 <= value <= 3600
        elif key == "near_interval": valid = type(value) is int and 30 <= value <= 300
        elif key == "low_threshold": valid = type(value) is int and 1 <= value <= 99
        elif key == "tray_style": valid = value in ("rings", "bars")
        elif key == "skin_id": valid = isinstance(value, str) and bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value))
        elif key == "window": valid = valid_window(value)
        elif key == "display_mode": valid = value in ("standard", "bar", "mini")
        elif key == "window_positions": valid = (isinstance(value, dict) and set(value).issubset({"standard", "bar", "mini"})
                                                   and all(isinstance(v, dict) and valid_window(v) for v in value.values()))
        elif key == "reminders": valid = isinstance(value, list) and all(type(v) is int and v in {1, 5, 10, 30} for v in value)
        if valid: defaults[key] = value
    config = Config(**defaults)
    positions = dict(config.window_positions)
    if config.window and "standard" not in positions:
        positions["standard"] = config.window
    config.window_positions = positions
    return accessible_config(config)


def accessible_config(config: Config) -> Config:
    """Keep at least one way to reach the application after startup."""
    if not config.show_on_start and not config.tray_enabled:
        return replace(config, tray_enabled=True) if config.autostart else replace(config, show_on_start=True)
    return config


def valid_window(value):
    return isinstance(value, dict) and (not value or (
        all(type(value.get(k)) is int and abs(value[k]) < 100000 for k in ("x", "y", "width", "height"))
        and value["width"] > 0 and value["height"] > 0
        and isinstance(value.get("screen", ""), str)))

def save_config(data_dir: Path, config: Config) -> None:
    config = accessible_config(config)
    d = Path(data_dir); d.mkdir(parents=True, exist_ok=True)
    p = d / "config.json"; tmp = p.with_suffix(".json.tmp")
    try:
        existing = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    values = existing if isinstance(existing, dict) else {}
    values.update(asdict(config))
    tmp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)

def _protect(data: bytes) -> bytes:
    if os.name != "nt": raise RuntimeError("Windows DPAPI is unavailable")
    class B(ctypes.Structure): _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    src = (ctypes.c_ubyte * len(data)).from_buffer_copy(data); inp=B(len(data), src); out=B()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)): raise OSError("DPAPI protect failed")
    try: return ctypes.string_at(out.pbData, out.cbData)
    finally: ctypes.windll.kernel32.LocalFree(out.pbData)

def _unprotect(data: bytes) -> bytes:
    if os.name != "nt": raise RuntimeError("Windows DPAPI is unavailable")
    class B(ctypes.Structure): _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    src = (ctypes.c_ubyte * len(data)).from_buffer_copy(data); inp=B(len(data), src); out=B()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)): raise OSError("DPAPI unprotect failed")
    try: return ctypes.string_at(out.pbData, out.cbData)
    finally: ctypes.windll.kernel32.LocalFree(out.pbData)

def save_webhook(data_dir: Path, url: str) -> None:
    if not isinstance(url, str): raise TypeError("webhook URL must be text")
    if url and not re.fullmatch(r"https://(?:discord\.com|discordapp\.com|canary\.discord\.com|ptb\.discord\.com)/api/webhooks/[0-9]{15,25}/[^/?#]+", url):
        raise ValueError("invalid Discord webhook URL")
    d=Path(data_dir); d.mkdir(parents=True, exist_ok=True)
    target = d / "webhook.bin"; tmp = target.with_suffix(".tmp")
    tmp.write_bytes(_protect(url.encode("utf-8"))); os.replace(tmp, target)

def load_webhook(data_dir: Path) -> str:
    try: return _unprotect((Path(data_dir)/"webhook.bin").read_bytes()).decode("utf-8")
    except (FileNotFoundError, OSError, UnicodeError, RuntimeError): return ""

_SECRET = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|webhook|cookie)(\s*[=:]\s*)([^,;\s}]+)")
_URL_SECRET = re.compile(r"https?://(?:[^\s/@]+(?::[^\s/@]*)?@)?(?:discord(?:app)?\.com|canary\.discord\.com|ptb\.discord\.com)/api/webhooks/[^\s\"']+")
def redact(value: Any) -> Any:
    if isinstance(value, dict): return {k: ("[REDACTED]" if re.search(r"(?i)(password|secret|token|key|authorization|webhook|cookie)", str(k)) else redact(v)) for k,v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    if isinstance(value, tuple): return tuple(redact(v) for v in value)
    if isinstance(value, str): return _SECRET.sub(r"\1\2[REDACTED]", _URL_SECRET.sub("[REDACTED_URL]", value))
    return value

def _date(value):
    if value is None:
        return "-"
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y/%m/%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return "-"

def _pct(value):
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "-"

class Database:
    def __init__(self, data_dir: Path):
        d=Path(data_dir); d.mkdir(parents=True, exist_ok=True); self.conn=sqlite3.connect(d/"rate_history.db")
        self.conn.execute("CREATE TABLE IF NOT EXISTS rates (id INTEGER PRIMARY KEY, fetched_at REAL, five_used REAL, five_remaining REAL, five_reset REAL, weekly_used REAL, weekly_remaining REAL, weekly_reset REAL, state TEXT, limit_type TEXT, version TEXT, snapshot TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, created_at REAL, kind TEXT, message TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS recoveries (event_key TEXT PRIMARY KEY, timestamp REAL, event_type TEXT, previous_remaining REAL, current_remaining REAL, delta REAL, five_hour_remaining REAL, weekly_remaining REAL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS notification_claims (key TEXT, channel TEXT, claimed_at REAL, PRIMARY KEY(key,channel))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, created_at REAL, kind TEXT, channel TEXT, result INTEGER, message TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS monitor_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)"); self.conn.commit()
    def record_rate(self, snapshot, state, version):
        five_window = getattr(snapshot, "five_hour", None)
        weekly_window = getattr(snapshot, "weekly", None)
        five = getattr(five_window, "remaining", None)
        weekly = getattr(weekly_window, "remaining", None)
        five_used = getattr(five_window, "used_percent", None)
        weekly_used = getattr(weekly_window, "used_percent", None)
        state_value = getattr(state, "value", state)
        self.conn.execute("INSERT INTO rates (fetched_at,five_used,five_remaining,five_reset,weekly_used,weekly_remaining,weekly_reset,state,limit_type,version,snapshot) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (time.time(), five_used, five, getattr(five_window, "reset_at", None), weekly_used, weekly, getattr(weekly_window, "reset_at", None), str(state_value), str(state_value), version, json.dumps(_jsonable(snapshot)))); self.conn.commit()
    def event(self, kind, message=""):
        import time; msg=redact(message); msg=msg if isinstance(msg,str) else json.dumps(msg,ensure_ascii=False); self.conn.execute("INSERT INTO events VALUES(NULL,?,?,?)", (time.time(),kind,msg)); self.conn.commit()
    def record_recovery(self, event):
        data = event.recovery
        self.conn.execute("INSERT OR IGNORE INTO recoveries VALUES(?,?,?,?,?,?,?,?)", (
            event.key, data["timestamp"], data["event_type"], data["previous_remaining"],
            data["current_remaining"], data["delta"], data["five_hour_remaining"], data["weekly_remaining"]))
        self.conn.commit()
    def claim_notification(self,key,channel):
        import time
        try: self.conn.execute("INSERT INTO notification_claims VALUES(?,?,?)",(key,channel,time.time())); self.conn.commit(); return True
        except sqlite3.IntegrityError: self.conn.rollback(); return False
    def notification(self,kind,channel,result,message):
        msg=redact(message); msg=msg if isinstance(msg,str) else json.dumps(msg,ensure_ascii=False)
        success = result if isinstance(result, bool) else str(result).lower() in {"1", "true", "ok", "success", "成功", "sent"}
        self.conn.execute("INSERT INTO notifications VALUES(NULL,?,?,?,?,?)",(time.time(),kind,channel,int(success),msg)); self.conn.commit()
    def release_notification(self, key, channel):
        self.conn.execute("DELETE FROM notification_claims WHERE key=? AND channel=?", (key, channel))
        self.conn.commit()
    def history(self,kind="rates",limit=300):
        if kind == "recoveries":
            rows = self.conn.execute("SELECT timestamp,event_type,previous_remaining,current_remaining,delta,five_hour_remaining,weekly_remaining FROM recoveries ORDER BY rowid DESC LIMIT ?", (int(limit),)).fetchall()
            return ["日時", "回復した枠", "前回", "今回", "増加", "5時間残量", "週間残量"], [(_date(r[0]), "5時間" if r[1] == "RATE_5H_RECOVERED" else "週間", _pct(r[2]), _pct(r[3]), "+" + _pct(r[4]), _pct(r[5]), _pct(r[6])) for r in rows]
        if kind == "notifications":
            rows = self.conn.execute("SELECT created_at,kind,channel,result,message FROM notifications ORDER BY id DESC LIMIT ?",(int(limit),)).fetchall()
            return ["日時", "種類", "チャンネル", "結果", "メッセージ"], [(_date(r[0]), r[1], r[2], "成功" if r[3] else "失敗", r[4]) for r in rows]
        if kind == "rates":
            rows = self.conn.execute("SELECT fetched_at,five_used,five_remaining,five_reset,weekly_used,weekly_remaining,weekly_reset,state FROM rates ORDER BY id DESC LIMIT ?",(int(limit),)).fetchall()
            return ["日時", "5時間使用率", "5時間残量", "5時間リセット", "週間使用率", "週間残量", "週間リセット", "状態"], [(_date(r[0]), _pct(r[1]), _pct(r[2]), _date(r[3]), _pct(r[4]), _pct(r[5]), _date(r[6]), r[7]) for r in rows]
        rows = self.conn.execute("SELECT created_at,kind,message FROM events ORDER BY id DESC LIMIT ?",(int(limit),)).fetchall()
        return ["日時", "種類", "メッセージ"], [(_date(r[0]), r[1], r[2]) for r in rows]
    def save_state(self, state: dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO monitor_state(id,data) VALUES(1,?)", (json.dumps(state),)); self.conn.commit()
    def load_state(self) -> dict:
        row=self.conn.execute("SELECT data FROM monitor_state WHERE id=1").fetchone()
        try: return json.loads(row[0]) if row else {}
        except (TypeError, json.JSONDecodeError): return {}
    def close(self): self.conn.close()

def _jsonable(x):
    if hasattr(x,"__dict__"): return {k:_jsonable(v) for k,v in vars(x).items()}
    if isinstance(x,(list,tuple)): return [_jsonable(v) for v in x]
    return x
