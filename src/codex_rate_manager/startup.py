from __future__ import annotations
import os, sys

def set_autostart(enabled: bool) -> None:
    if os.name != "nt": return
    import winreg
    key_path=r"Software\Microsoft\Windows\CurrentVersion\Run"
    name="Codex Rate Manager"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,key_path) as key:
        if not enabled:
            try: winreg.DeleteValue(key,name)
            except FileNotFoundError: pass
            return
        if getattr(sys,"frozen",False): cmd=f'"{sys.executable}" --autostart'
        else:
            exe=os.path.join(os.path.dirname(sys.executable),"pythonw.exe")
            if not os.path.isfile(exe):
                raise FileNotFoundError(f"pythonw.exe not found: {exe}")
            cmd=f'"{exe}" -m codex_rate_manager --autostart'
        winreg.SetValueEx(key,name,0,winreg.REG_SZ,cmd)
