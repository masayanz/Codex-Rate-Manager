$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path '.venv\Scripts\python.exe')) { py -3.13 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw '依存関係の確認に失敗しました。' }
& .\.venv\Scripts\python.exe -m pytest
if ($LASTEXITCODE -ne 0) { throw 'テストが失敗しました。' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name CodexRateManager --paths src --collect-all windows_toasts scripts\entry.py
if ($LASTEXITCODE -ne 0) { throw 'EXEの生成に失敗しました。' }
Write-Output '生成先: dist\CodexRateManager\CodexRateManager.exe'
