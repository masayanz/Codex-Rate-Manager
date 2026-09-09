param([switch]$Mock)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path '.venv\Scripts\python.exe')) { py -3.13 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw '依存関係のインストールに失敗しました。' }
$appArgs = @('-m', 'codex_rate_manager')
if ($Mock) { $appArgs += '--mock' }
& .\.venv\Scripts\python.exe @appArgs
