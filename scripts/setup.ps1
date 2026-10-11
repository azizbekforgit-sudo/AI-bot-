<#
  Claude Desktop Assistant — installation for the current user (no administrator rights needed).

  What it does:
    1. Creates a Python virtual environment in .venv and installs dependencies.
    2. Downloads the offline Russian speech model (~45 MB).
    3. Checks that Claude Code is installed and tells you how to sign in.
    4. Creates a Start menu shortcut.
    5. With -Autostart: enables start at sign-in (HKCU Run key, visible in Task Manager).

  Usage (PowerShell, from the project folder):
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Autostart
#>
param(
    [switch]$Autostart,
    [switch]$NoShortcut
)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

Step "Проверяю Python"
$pyExe = $null
$pyArgs = @()
foreach ($candidate in @(@("py", "-3.12"), @("py", "-3.11"), @("py", "-3"), @("python"))) {
    $exe = $candidate[0]
    $extra = @($candidate | Select-Object -Skip 1)
    try {
        $version = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -and [version]$version -ge [version]"3.10") {
            $pyExe = $exe; $pyArgs = $extra; break
        }
    } catch { }
}
if (-not $pyExe) {
    Write-Host "Нужен Python 3.10 или новее: https://www.python.org/downloads/ (отметьте 'Add python.exe to PATH')." -ForegroundColor Red
    exit 1
}
Write-Host "Найден Python $version"

Step "Создаю виртуальное окружение .venv"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $pyExe @pyArgs -m venv .venv
}
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
Write-Host "Скачиваю и устанавливаю библиотеки (около 300 МБ, в основном Qt для интерфейса)."
Write-Host "Это может занять 3-10 минут в зависимости от интернета. Не закрывайте окно."
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -e .
if ($LASTEXITCODE -ne 0) { Write-Host "Не удалось установить зависимости." -ForegroundColor Red; exit 1 }

Step "Скачиваю модель распознавания русской речи"
& $venvPy scripts\download_model.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Модель не скачалась. Голосовое управление будет недоступно, текстовое — работает." -ForegroundColor Yellow
}

Step "Проверяю Claude Code"
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    Write-Host "Claude Code найден: $($claude.Source)"
    Write-Host "Если вы ещё не входили: откройте новый терминал, выполните 'claude' и войдите в аккаунт Claude Pro."
} else {
    Write-Host "Claude Code не найден. Установите его (официальный установщик Anthropic):" -ForegroundColor Yellow
    Write-Host "    irm https://claude.ai/install.ps1 | iex" -ForegroundColor Yellow
    Write-Host "затем откройте новый терминал, выполните 'claude' и войдите в аккаунт Claude Pro."
}

Step "Проверяю русский голос Windows"
$voices = & $venvPy -c "import win32com.client as w; v=w.Dispatch('SAPI.SpVoice').GetVoices(); print(';'.join(v.Item(i).GetDescription() for i in range(v.Count)))" 2>$null
if ($voices -match "Irina|Pavel|Russian") {
    Write-Host "Русский голос установлен."
} else {
    Write-Host "Русский голос не найден. Установите: Параметры -> Время и язык -> Речь -> Добавить голоса -> Русский." -ForegroundColor Yellow
}

if (-not $NoShortcut) {
    Step "Создаю ярлык в меню «Пуск»"
    $pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
    $lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "Claude Desktop Assistant.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($lnk)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = "-m claude_assistant"
    $shortcut.WorkingDirectory = $Root
    $shortcut.Description = "Claude Desktop Assistant"
    $shortcut.Save()
    Write-Host "Ярлык: $lnk"
}

if ($Autostart) {
    Step "Включаю автозапуск при входе в Windows"
    & $venvPy -m claude_assistant --enable-autostart
}

Write-Host "`nГотово. Запуск: scripts\run.ps1 или ярлык «Claude Desktop Assistant» в меню «Пуск»." -ForegroundColor Green
