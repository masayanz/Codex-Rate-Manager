# Codex Rate Manager

- Windows専用。WSL2を前提にせず、PowerShellを優先し、`.sh`を作らない。
- Python 3.13 / PySide6 / SQLite / PyInstallerを使用する。
- コミットは明示依頼時のみ、日本語で行う。pushは別途依頼時のみ。
- Codexグローバル設定や認証情報を変更・保存しない。
- Discord Webhook URLをログや平文設定へ出力しない。DPAPIで保存する。
- Codex通信、HTTP、DB処理、プロセス待機をGUIスレッドで実行しない。
- リセット時刻到達だけでは利用可能と判定しない。両枠の再取得が必要。
- アプリが起動したApp Server以外のプロセスを終了しない。
- 検証: `.venv\Scripts\python.exe -m pytest`。ビルド: `scripts\build.ps1`。
- 実機確認していない機能を確認済み扱いしない。
