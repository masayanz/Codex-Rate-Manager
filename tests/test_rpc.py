import json
import sys
import textwrap
import time

import pytest

from codex_rate_manager.codex import AppServer


def fake_server(tmp_path, mode="normal"):
    script = tmp_path / "fake_server.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys, time
        for line in sys.stdin:
            m = json.loads(line)
            if m.get('method') == 'initialize':
                if {mode!r} == 'timeout': time.sleep(2)
                print(json.dumps({{'jsonrpc':'2.0','id':m['id'],'result':{{'userAgent':'fake/1'}}}}), flush=True)
            elif m.get('method') == 'account/rateLimits/read':
                print(json.dumps({{'jsonrpc':'2.0','id':m['id'],'result':{{'rateLimits':{{'primary':{{'usedPercent':1,'windowDurationMins':300}},'secondary':{{'usedPercent':2,'windowDurationMins':10080}}}}}}}}), flush=True)
                print(json.dumps({{'jsonrpc':'2.0','method':'account/rateLimits/updated','params':{{'rateLimits':{{}}}}}}), flush=True)
            elif m.get('method') == 'initialized' and {mode!r} == 'eof':
                break
    """), encoding="utf-8")
    return [sys.executable, str(script)]


def test_initialize_read_and_update_queue(tmp_path):
    client = AppServer(executable=fake_server(tmp_path)[0], timeout=2)
    # The fixture needs its script argument, so invoke Python directly through a wrapper path.
    client.executable = sys.executable
    original = client.start
    def start():
        import subprocess, threading
        client._stop.clear()
        client.process = subprocess.Popen(fake_server(tmp_path), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
        client._reader = threading.Thread(target=client._read_loop, daemon=True); client._reader.start()
        initialized = client.request("initialize", {"clientInfo": {"name": "codex_rate_manager", "version": "0.1"}})
        client.version = initialized.get("userAgent")
        client.notify("initialized", {})
    client.start = start
    client.start()
    assert client.version == "fake/1"
    result = client.read_rates()
    assert result["rateLimits"]["primary"]["windowDurationMins"] == 300
    assert client.updates.get(timeout=1)["method"] == "account/rateLimits/updated"
    client.close()
    assert client.pid is None


def test_timeout_and_close_cleanup(tmp_path):
    client = AppServer(executable="", timeout=0.1)
    client.executable = sys.executable
    import subprocess, threading
    client.process = subprocess.Popen(fake_server(tmp_path, "timeout"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
    client._reader = threading.Thread(target=client._read_loop, daemon=True); client._reader.start()
    with pytest.raises(TimeoutError):
        client.request("initialize")
    client.close()
    assert client.pid is None


def test_eof_unblocks_pending_request(tmp_path):
    client = AppServer(executable=sys.executable, timeout=1)
    import subprocess, threading
    client.process = subprocess.Popen(fake_server(tmp_path, "eof"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
    client._reader = threading.Thread(target=client._read_loop, daemon=True); client._reader.start()
    client.request("initialize")
    # The server exits after initialized; a subsequent request sees EOF promptly.
    client.notify("initialized", {})
    time.sleep(0.1)
    with pytest.raises((RuntimeError, TimeoutError)):
        client.request("account/rateLimits/read")
    client.close()
