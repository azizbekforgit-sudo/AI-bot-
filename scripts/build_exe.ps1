<#
  Build a standalone Windows folder (dist\ClaudeDesktopAssistant\ClaudeDesktopAssistant.exe) with PyInstaller.
  Run scripts\setup.ps1 first. The speech model is downloaded separately (scripts\download_model.py).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Host "Сначала запустите scripts\setup.ps1" -ForegroundColor Red; exit 1 }
& $venvPy -m pip install "pyinstaller>=6.3,<7" --quiet
& $venvPy -m PyInstaller packaging\claude_assistant.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Write-Host "Сборка не удалась." -ForegroundColor Red; exit 1 }
$exe = Join-Path $Root "dist\ClaudeDesktopAssistant\ClaudeDesktopAssistant.exe"
& $exe --version
Write-Host "Готово: $exe" -ForegroundColor Green
