"""Small JSON-RPC App Server client used from worker threads."""
from __future__ import annotations

import itertools
import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any


def find_codex() -> str | None:
    for name in ("codex.exe", "codex", "codex.cmd"):
        found = shutil.which(name)
        if found:
            return found
    return None


class AppServer:
    def __init__(self, executable: str | None = None, timeout: float = 10.0):
        self.executable = executable or find_codex()
        self.timeout = timeout
        self.version: str | None = None
        self.process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._responses: dict[int, queue.Queue[Any]] = {}
        self._lock = threading.Lock()
        self.updates: queue.Queue[dict[str, Any]] = queue.Queue()
        self._ids = itertools.count(1)

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        if not self.executable:
            raise FileNotFoundError("codex executable was not found")
        self._stop.clear()
        command = [self.executable, "app-server"]
        if self.executable.lower().endswith(".cmd"):
            native = str(Path(self.executable).with_suffix(".exe"))
            if Path(native).is_file():
                command = [native, "app-server"]
            else:
                # Keep shell parsing disabled.  cmd.exe receives one quoted,
                # validated path and the fixed app-server argument.
                if '"' in self.executable:
                    raise ValueError("invalid Codex executable path")
                command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", f'"{self.executable}" app-server']
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._reader = threading.Thread(target=self._read_loop, name="codex-app-server-reader", daemon=True)
        self._reader.start()
        initialized = self.request("initialize", {"clientInfo": {"name": "codex_rate_manager", "version": "0.1"}})
        if isinstance(initialized, dict):
            info = initialized.get("serverInfo", initialized.get("server_info", {}))
            if isinstance(info, dict) and info.get("version") is not None:
                self.version = str(info["version"])
            elif isinstance(initialized.get("userAgent"), str):
                self.version = initialized["userAgent"]
        self.notify("initialized", {})

    def _read_loop(self) -> None:
        proc = self.process
        if not proc or not proc.stdout:
            return
        while not self._stop.is_set():
            line = proc.stdout.readline()
            if not line:
                error = RuntimeError("App Server closed its output")
                with self._lock:
                    waiters = list(self._responses.values())
                for waiter in waiters:
                    waiter.put({"error": str(error)})
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            ident = message.get("id")
            if isinstance(ident, int):
                with self._lock:
                    waiter = self._responses.get(ident)
                if waiter:
                    waiter.put(message)
            elif isinstance(message, dict) and message.get("method") in {"account/rateLimits/updated", "account/rateLimits/updatedNotification"}:
                self.updates.put(message)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            raise RuntimeError("App Server is not running")
        ident = next(self._ids)
        waiter: queue.Queue[Any] = queue.Queue(maxsize=1)
        with self._lock:
            self._responses[ident] = waiter
        try:
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            try:
                response = waiter.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"App Server request timed out: {method}") from exc
            if "error" in response:
                raise RuntimeError(str(response["error"]))
            return response.get("result", response)
        finally:
            with self._lock:
                self._responses.pop(ident, None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self.process and self.process.stdin:
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}, separators=(",", ":")) + "\n")
            self.process.stdin.flush()

    def read_rate_limits(self) -> dict[str, Any]:
        return self.request("account/rateLimits/read")

    read_rates = read_rate_limits

    def stop(self) -> None:
        self._stop.set()
        proc = self.process
        if not proc:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=1)
        self.process = None

    close = stop


# Descriptive alias for callers that prefer an explicit client name.
AppServerClient = AppServer
