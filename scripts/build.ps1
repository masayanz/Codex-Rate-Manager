$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = [IO.Path]::GetFullPath((Join-Path (Get-Location) '.venv\Scripts\python.exe'))
if (!(Test-Path $python)) { py -3.13 -m venv .venv }
$python = [IO.Path]::GetFullPath((Join-Path (Get-Location) '.venv\Scripts\python.exe'))
& $python -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw '依存関係の確認に失敗しました。' }
& $python -m pytest
if ($LASTEXITCODE -ne 0) { throw 'テストが失敗しました。' }

& $python scripts\generate_icon.py
if ($LASTEXITCODE -ne 0) { throw 'アプリケーションアイコンの生成に失敗しました。' }

# 既存のdist配下を削除せず、ビルド前に退避する。履歴DBなどが置かれていても復元可能にする。
$workspacePath = [IO.Path]::GetFullPath((Get-Location).Path)
$distPath = [IO.Path]::GetFullPath((Join-Path $workspacePath 'dist'))
$buildPath = [IO.Path]::GetFullPath((Join-Path $workspacePath 'build'))
$cachePath = [IO.Path]::GetFullPath((Join-Path $workspacePath '.cache'))
$workspacePrefix = $workspacePath.TrimEnd('\') + '\'

function Assert-SafeWorkspacePath([string]$path, [string]$label) {
    if (!(Test-Path -LiteralPath $path)) { return }
    $item = Get-Item -LiteralPath $path -Force
    $resolved = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $path).Path)
    if ($resolved -ne $path -or !$resolved.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$label の実体パスがワークスペース外です: $resolved"
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.LinkType) {
        throw "$label にシンボリックリンク等は使用できません: $path"
    }
}

Assert-SafeWorkspacePath $distPath 'dist'
Assert-SafeWorkspacePath $buildPath 'build'
Assert-SafeWorkspacePath $cachePath '.cache'

$preservePath = [IO.Path]::GetFullPath((Join-Path $cachePath ('build-preserved\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))))
if (!$preservePath.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw '退避先がワークスペース外です。'
}
if (Test-Path $distPath) {
    $oldItems = @(Get-ChildItem -LiteralPath $distPath -Force)
    if ($oldItems.Count -gt 0) {
        New-Item -ItemType Directory -Path $preservePath -Force | Out-Null
        foreach ($item in $oldItems) {
            try {
                Move-Item -LiteralPath $item.FullName -Destination $preservePath
            } catch {
                throw '旧配布物を退避できません。Codex Rate Managerをタスクトレイの「終了」から終了してから、ビルドを再実行してください。'
            }
        }
    }
} else {
    New-Item -ItemType Directory -Path $distPath -Force | Out-Null
}
if (Test-Path $buildPath) { Remove-Item -LiteralPath $buildPath -Recurse -Force }

$oldPath = $env:PATH
try {
    $basePrefix = (& $python -c "import sys; print(sys.base_prefix)").Trim()
    $pythonDir = Split-Path (& $python -c "import sys; print(sys._base_executable)").Trim()
    $venvScripts = Split-Path $python
    $env:PATH = ($venvScripts, $pythonDir, "$env:SystemRoot\System32", $env:SystemRoot, "$env:SystemRoot\System32\Wbem") -join ';'
    Write-Output ('Python executable: ' + (& $python -c "import sys; print(sys.executable)").Trim())
    Write-Output ('Python version: ' + (& $python -c "import sys; print(sys.version)").Trim())
    Write-Output ('sys.base_prefix: ' + $basePrefix)
    Write-Output ('PySide6 version: ' + (& $python -c "import PySide6; print(PySide6.__version__)").Trim())
    Write-Output ('PyInstaller version: ' + (& $python -c "import PyInstaller; print(PyInstaller.__version__)").Trim())
    & $python -m PyInstaller --noconfirm --clean CodexRateManager.spec
} finally {
    $env:PATH = $oldPath
}
if ($LASTEXITCODE -ne 0) { throw 'EXEの生成に失敗しました。' }
$exePath = Join-Path $distPath 'CodexRateManager.exe'
if (!(Test-Path $exePath -PathType Leaf)) { throw "EXEが見つかりません: $exePath" }
$distItems = @(Get-ChildItem -LiteralPath $distPath -Force)
if ($distItems.Count -ne 1 -or $distItems[0].Name -ne 'CodexRateManager.exe') {
    throw 'distにはCodexRateManager.exeのみを生成する必要があります。'
}
Write-Output '生成先: dist\CodexRateManager.exe'
