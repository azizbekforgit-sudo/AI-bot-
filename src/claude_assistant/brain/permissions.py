"""Autonomy levels → Claude Code permission flags, and review of denied actions.

Claude Code runs headless (`claude -p`). Anything not explicitly allowed is
denied automatically and reported back in `permission_denials`. The assistant
shows each denial to the user; an approved one is re-run with an exact,
narrowly scoped allow rule. Some actions are never approvable (security
controls, privilege escalation, persistence, mass deletion).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

READ_ONLY = ["Read", "Glob", "Grep", "TodoWrite"]

BALANCED = READ_ONLY + [
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "WebSearch",
    "WebFetch",
    *[
        f"{shell}({cmd})"
        for shell in ("Bash", "PowerShell")
        for cmd in (
            "git status", "git status *", "git diff", "git diff *", "git log *", "git show *",
            "git branch", "git branch --list *",
            "ls", "ls *", "dir", "dir *", "pwd", "where *", "which *",
            "python --version", "py --version", "node --version", "npm --version",
            "python -m pytest", "python -m pytest *", "pytest", "pytest *", "py -m pytest *",
            "npm test", "npm test *", "npm run test *", "npm run lint *", "npm run build *",
        )
    ],
    "PowerShell(Get-ChildItem *)",
    "PowerShell(Get-Content *)",
    "PowerShell(Get-Process *)",
    "PowerShell(Get-Location)",
    "PowerShell(Test-Path *)",
]

ADVANCED = BALANCED + [
    f"{shell}({cmd})"
    for shell in ("Bash", "PowerShell")
    for cmd in (
        "git add *", "git commit *", "git checkout *", "git switch *", "git stash *", "git restore *",
        "python *", "py *", "node *", "npm run *", "npx tsc *", "mkdir *", "cp *", "mv *",
        "dotnet build *", "dotnet test *", "cargo build *", "cargo test *", "go build *", "go test *",
    )
]

# "Разрешать всё": every tool is allowed. Safety then rests on HARD_DENY_RULES plus the
# PreToolUse guard hook (brain/guard.py), which runs before every command and file edit.
FULL = ADVANCED + ["Bash", "PowerShell", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch"]

# Passed as --disallowedTools so that even a broad allow rule cannot reach them.
HARD_DENY_RULES = [
    f"{shell}({cmd})"
    for shell in ("Bash", "PowerShell")
    for cmd in (
        "Set-MpPreference *", "Add-MpPreference *", "netsh advfirewall *", "netsh firewall *",
        "bcdedit *", "diskpart *", "format *", "vssadmin *", "cipher *", "takeown *",
        "reg delete *", "reg add HKLM*", "reg add HKEY_LOCAL_MACHINE*", "reg save *",
        "runas *", "sudo *", "schtasks *", "sc *", "net user *", "net localgroup *",
        "Set-ExecutionPolicy *", "Disable-NetFirewallProfile *", "Set-NetFirewallProfile *",
        "shutdown *", "Stop-Computer *", "Restart-Computer *",
    )
] + [
    "Read(**/.env)",
    "Read(**/.env.*)",
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(~/.claude/.credentials.json)",
    "Read(**/id_rsa*)",
    "Read(**/*.pem)",
    "Read(**/*.key)",
]

_FORBIDDEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.IGNORECASE), reason)
    for p, reason in [
        (r"set-mppreference|add-mppreference|disableantispyware|disablerealtimemonitoring", "отключение антивируса"),
        (r"netsh\s+(advfirewall|firewall)|(set|disable)-netfirewallprofile", "изменение брандмауэра"),
        (r"enablelua|consentpromptbehavior|bcdedit|diskpart|\bvssadmin\b|cipher\s+/w", "изменение защиты системы"),
        (r"\bformat(\.com)?\s+[a-z]:", "форматирование диска"),
        (r"\brunas\b|\bsudo\b|-verb\s+runas|\bpsexec\b", "повышение прав администратора"),
        (r"\bschtasks\b|\\currentversion\\run|new-service|\bsc(\.exe)?\s+(create|config)", "установка автозапуска или службы"),
        (r"reg(\.exe)?\s+(add|delete)\s+(hklm|hkey_local_machine)", "изменение системного реестра"),
        (r"reg(\.exe)?\s+save|mimikatz|lsass|\\sam\b|procdump", "доступ к учётным данным"),
        (r"\bnet\s+(user|localgroup)\b", "изменение учётных записей"),
        (r"(curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b[^|]*\|\s*(sh|bash|iex|invoke-expression|powershell)", "запуск скрипта из интернета"),
        (r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$home|c:)\s*($|[\s/\\]\s*$)", "удаление корня диска или домашней папки"),
        (r"(del|erase|rd|rmdir)\s+.*/s.*\s[a-z]:\\?\s*$", "удаление всего диска"),
        (r"remove-item\s+.*-recurse.*\s([a-z]:\\?|~|\$home|\$env:userprofile)\s*$", "удаление всего диска или домашней папки"),
        (r"set-executionpolicy\s+(unrestricted|bypass)", "отключение политики выполнения скриптов"),
    ]
]

_HIGH_RISK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.IGNORECASE), reason)
    for p, reason in [
        (r"\b(rm|del|erase|rd|rmdir|remove-item|unlink)\b", "удаление файлов"),
        (r"\b(pip|pip3|npm|pnpm|yarn|winget|choco|scoop|cargo|gem)\s+(install|add|i)\b|\bmsiexec\b", "установка программ"),
        (r"\bgit\s+(push|reset\s+--hard|clean|rebase|filter-branch)\b", "необратимое действие git"),
        (r"\b(curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm|scp|ssh|ftp)\b", "обмен данными по сети"),
        (r"\b(taskkill|stop-process|kill)\b", "завершение процессов"),
        (r"\breg(\.exe)?\s+(add|delete)\b|set-itemproperty", "изменение реестра"),
        (r"\b(mail|send-mailmessage)\b", "отправка сообщений"),
    ]
]

_SHELL_TOOLS = {"Bash", "PowerShell"}
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


@dataclass
class Denial:
    tool_name: str
    tool_input: dict[str, Any]
    summary: str
    risk: str  # "normal" | "high" | "forbidden"
    reason: str = ""
    allow_rule: str | None = None
    extra_dirs: list[str] = field(default_factory=list)

    @property
    def approvable(self) -> bool:
        return self.risk != "forbidden" and self.allow_rule is not None


def rules_for(autonomy: str, extra_rules: list[str] | None = None) -> tuple[str, list[str]]:
    """(permission_mode, allowed_tools) for an autonomy level."""
    if autonomy == "assisted":
        mode, rules = "plan", list(READ_ONLY)
    elif autonomy == "advanced":
        mode, rules = "acceptEdits", list(ADVANCED)
    elif autonomy == "full":
        mode, rules = "acceptEdits", list(FULL)
    else:
        mode, rules = "acceptEdits", list(BALANCED)
    for rule in extra_rules or []:
        if rule not in rules:
            rules.append(rule)
    return mode, rules


def _within(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


_UNSAFE_RULE_CHARS = re.compile(r"[\r\n\x00]")


def review_denial(tool_name: str, tool_input: dict[str, Any], workspaces: list[Path]) -> Denial:
    """Describe a denied tool call in plain Russian and decide whether it can be approved."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}

    if tool_name in _SHELL_TOOLS:
        command = str(tool_input.get("command", "")).strip()
        summary = f"Выполнить команду: {command[:300]}"
        for pattern, reason in _FORBIDDEN_PATTERNS:
            if pattern.search(command):
                return Denial(tool_name, tool_input, summary, "forbidden", f"Запрещено всегда: {reason}.")
        if not command or _UNSAFE_RULE_CHARS.search(command) or len(command) > 1000:
            return Denial(
                tool_name, tool_input, summary, "forbidden",
                "Многострочную команду нельзя разрешить точечно. Выполните её вручную, если она нужна.",
            )
        risk, reason = "normal", "Команда не входит в список разрешённых для текущего уровня автономии."
        for pattern, why in _HIGH_RISK_PATTERNS:
            if pattern.search(command):
                risk, reason = "high", f"Внимание: {why}."
                break
        return Denial(tool_name, tool_input, summary, risk, reason, allow_rule=f"{tool_name}({command})")

    if tool_name in _EDIT_TOOLS:
        target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        summary = f"Изменить файл: {target}"
        if not target or not _within(Path(target), workspaces):
            return Denial(
                tool_name, tool_input, summary, "forbidden",
                "Файл вне разрешённых рабочих папок. Добавьте папку в «Настройки → Рабочие папки», если доверяете ей.",
            )
        return Denial(tool_name, tool_input, summary, "normal", "Изменение файла в рабочей папке.", allow_rule=tool_name)

    if tool_name == "WebFetch":
        url = str(tool_input.get("url", ""))
        host = urlparse(url).hostname or ""
        summary = f"Открыть веб-страницу: {url[:200]}"
        if not host or not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host):
            return Denial(tool_name, tool_input, summary, "forbidden", "Некорректный адрес.")
        return Denial(tool_name, tool_input, summary, "normal", "Загрузка страницы из интернета.",
                      allow_rule=f"WebFetch(domain:{host})")

    if tool_name == "Read":
        target = str(tool_input.get("file_path", ""))
        return Denial(tool_name, tool_input, f"Прочитать файл: {target}", "high",
                      "Файл может содержать личные данные или секреты.",
                      allow_rule=f"Read({target})" if target and not _UNSAFE_RULE_CHARS.search(target) else None)

    if tool_name == "ExitPlanMode":
        plan = str(tool_input.get("plan", ""))[:600]
        return Denial(tool_name, tool_input, f"Выполнить предложенный план:\n{plan}", "normal",
                      "Режим «С подтверждением»: план готов, нужно ваше согласие.", allow_rule="__execute_plan__")

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,60}", tool_name or ""):
        return Denial(tool_name, tool_input, f"Инструмент {tool_name!r}", "forbidden", "Неизвестный инструмент.")
    return Denial(tool_name, tool_input, f"Использовать инструмент {tool_name}", "normal",
                  "Инструмент не входит в разрешённые.", allow_rule=tool_name)
