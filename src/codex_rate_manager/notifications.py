from __future__ import annotations
import json
import math
import re
import time
import urllib.parse

import requests

def validate_webhook(url: str) -> bool:
    """Return true only for a Discord webhook URL with numeric id and token."""
    if not isinstance(url, str) or len(url) > 512:
        return False
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if p.scheme != "https" or p.query or p.fragment or p.username or p.password:
        return False
    if p.netloc.lower() not in {"discord.com","discordapp.com","canary.discord.com","ptb.discord.com"}:
        return False
    return bool(re.fullmatch(r"/api/webhooks/[0-9]{15,25}/[A-Za-z0-9_.-]+", p.path))

_valid = validate_webhook

class DiscordClient:
    def send(self, url, title, message, stop_event=None):
        if not validate_webhook(url):
            return False, "invalid Discord webhook URL"
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            return False, "cancelled"
        payload = {
            "username": "Codex Rate Manager",
            "embeds": [{"title": str(title), "description": str(message)}],
        }
        retry_delay = 0.0
        for attempt in range(3):
            if retry_delay:
                if stop_event and stop_event.wait(retry_delay): return False, "cancelled"
                if not stop_event: time.sleep(retry_delay)
                retry_delay = 0.0
            try:
                r=requests.post(url,json=payload,timeout=15,allow_redirects=False)
                if 200 <= r.status_code < 300: return True,f"Discord HTTP {r.status_code}"
                if r.status_code == 429 and attempt < 2:
                    try:
                        retry = float(r.headers.get("Retry-After") or r.json().get("retry_after"))
                        if not math.isfinite(retry) or retry < 0:
                            raise ValueError
                    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                        retry = (5, 15)[attempt]
                    if attempt < 2:
                        if stop_event and stop_event.wait(max(0, retry)): return False,"cancelled"
                        if not stop_event: time.sleep(max(0, retry))
                        continue
                if r.status_code == 408 or r.status_code >= 500:
                    if attempt < 2:
                        retry_delay = (5, 15)[attempt]
                        continue
                return False,f"Discord HTTP {r.status_code}"
            except requests.RequestException as e:
                if attempt == 2: return False,f"Discord request failed: {type(e).__name__}"
                retry_delay = (5, 15)[attempt]
        return False,"Discord request failed"


class NotificationManager:
    """手動テストも自動イベントも同じ配信・記録経路へ渡す。"""

    def __init__(self, enqueue, log):
        self.enqueue = enqueue
        self.log = log

    def send(self, event, config, url):
        if event.kind == "internal":
            self.log("NOTIFICATION_SKIPPED", f"event_type={event.event_type} notification_event_key={event.key} reason=still_limited_or_coalesced")
            return
        for channel in ("WINDOWS", "DISCORD"):
            if event.kind == "test":
                enabled = channel == "DISCORD"
                reason = "manual_test"
            else:
                enabled = config.notifications_enabled and (channel == "WINDOWS" or config.discord_enabled)
                enabled = enabled and (event.kind == "reminder" or getattr(config, f"{channel.lower()}_{event.kind}", False))
                reason = "enabled" if enabled else "settings_disabled"
            self.log("NOTIFICATION_ROUTING", f"event_type={event.event_type or event.kind} notification_event_key={event.key} channel={channel} notifications_enabled={config.notifications_enabled} discord_enabled={config.discord_enabled} discord_reset_enabled={config.discord_reset} enabled={enabled} reason={reason}")
            if enabled:
                self.enqueue(channel, event, url)

def send_windows(title, message):
    try:
        from windows_toasts import Toast, WindowsToaster, ToastDisplayImage
        from .resources import resource_path
        t = WindowsToaster("Codex Rate Manager")
        toast = Toast()
        toast.text_fields = [str(title), str(message)]
        logo = resource_path("assets/app.png")
        if logo.is_file():
            toast.AddImage(ToastDisplayImage.fromPath(logo, altText="Codex Rate Manager"))
        t.show_toast(toast)
        return True, "Windowsへ通知を登録しました（表示はWindows設定に依存）"
    except Exception as e:
        return False, f"Windows notification unavailable: {type(e).__name__}"
