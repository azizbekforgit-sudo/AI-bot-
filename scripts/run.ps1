<#
  Start the assistant from the project folder.
    scripts\run.ps1            — normal start (no console window)
    scripts\run.ps1 -Console   — with a console and debug logging, for troubleshooting
#>
param([switch]$Console)
$Root = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
    Write-Host "Сначала выполните установку: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1" -ForegroundColor Red
    exit 1
}
if ($Console) {
    & "$Root\.venv\Scripts\python.exe" -m claude_assistant --debug
} else {
    Start-Process -FilePath "$Root\.venv\Scripts\pythonw.exe" -ArgumentList "-m", "claude_assistant" -WorkingDirectory $Root
}
