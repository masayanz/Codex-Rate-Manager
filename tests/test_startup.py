from contextlib import nullcontext
import os
import sys

import pytest

from codex_rate_manager.startup import set_autostart


@pytest.mark.skipif(os.name != "nt", reason="Windows registry")
def test_autostart_uses_current_user_and_quoted_frozen_path(monkeypatch):
    import winreg
    created, written = [], []
    monkeypatch.setattr(winreg, "CreateKey", lambda hive, path: (created.append((hive, path)) or nullcontext("key")))
    monkeypatch.setattr(winreg, "SetValueEx", lambda *args: written.append(args))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Example\CodexRateManager.exe")
    set_autostart(True)
    assert created[0][0] == winreg.HKEY_CURRENT_USER
    assert created[0][1].endswith(r"CurrentVersion\Run")
    assert written[0][-1] == '"C:\\Program Files\\Example\\CodexRateManager.exe" --autostart'


@pytest.mark.skipif(os.name != "nt", reason="Windows registry")
def test_disable_missing_autostart_is_safe(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "CreateKey", lambda *args: nullcontext("key"))
    def missing(*args):
        raise FileNotFoundError
    monkeypatch.setattr(winreg, "DeleteValue", missing)
    set_autostart(False)
