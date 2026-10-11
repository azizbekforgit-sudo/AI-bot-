<#
  Remove Claude Desktop Assistant from this user account.

    scripts\uninstall.ps1               — disable autostart, remove Start menu shortcut and .venv
    scripts\uninstall.ps1 -RemoveData   — also delete settings, memory, logs and the speech model
                                           (%APPDATA%\ClaudeDesktopAssistant), after confirmation

  The project folder itself and Claude Code are left untouched.
#>
param([switch]$RemoveData)
$Root = Split-Path $PSScriptRoot -Parent

$running = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*claude_assistant*" }
if ($running) {
    Write-Host "Ассистент запущен. Закройте его: значок в трее -> Выход, затем повторите." -ForegroundColor Yellow
    exit 1
}

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $runKey -Name "ClaudeDesktopAssistant" -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $runKey -Name "ClaudeDesktopAssistant"
    Write-Host "Автозапуск отключён."
}

$lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "Claude Desktop Assistant.lnk"
if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Ярлык удалён." }

if (Test-Path "$Root\.venv") {
    Remove-Item "$Root\.venv" -Recurse -Force
    Write-Host "Виртуальное окружение удалено."
}

$data = Join-Path $env:APPDATA "ClaudeDesktopAssistant"
if ($RemoveData -and (Test-Path $data)) {
    $answer = Read-Host "Удалить настройки, память, логи и модель речи в $data ? (да/нет)"
    if ($answer -eq "да" -or $answer -eq "y" -or $answer -eq "yes") {
        Remove-Item $data -Recurse -Force
        Write-Host "Данные удалены."
    }
} elseif (Test-Path $data) {
    Write-Host "Данные сохранены в $data (удалить: -RemoveData)."
}
Write-Host "Готово."
