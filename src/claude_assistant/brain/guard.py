"""PreToolUse hook for Claude Code: the last line of defence, active at every autonomy level.

Claude Code runs this before each shell command, file edit and file read. Exit
code 2 blocks the call and the message is shown to Claude. It enforces the
"never" list even when the user has chosen "Разрешать всё".
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from claude_assistant.brain.permissions import _FORBIDDEN_PATTERNS

WORKSPACES_ENV = "CLAUDE_ASSISTANT_WORKSPACES"

_SECRET_PATH = re.compile(
    r"(^|[\\/])(\.env(\.[\w.-]+)?|id_rsa[\w.]*|id_ed25519[\w.]*|\.credentials\.json|credentials|"
    r"[\w.-]+\.(pem|key|pfx|p12|kdbx))$|[\\/]\.(ssh|aws|gnupg)[\\/]|Login Data$|Cookies$",
    re.IGNORECASE,
)


def _within(path: str, roots: list[Path]) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def check(tool_name: str, tool_input: dict, workspaces: list[Path]) -> str | None:
    """Reason to block the call, or None to let Claude Code's own permissions decide."""
    if tool_name in ("Bash", "PowerShell"):
        command = str(tool_input.get("command", ""))
        for pattern, reason in _FORBIDDEN_PATTERNS:
            if pattern.search(command):
                return f"Заблокировано ассистентом: {reason}. Это действие запрещено всегда — не пытайся обойти запрет."
        return None
    target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if tool_name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit") and target and _SECRET_PATH.search(target):
        return "Заблокировано ассистентом: файл может содержать пароли или ключи."
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit") and target and workspaces \
            and not _within(target, workspaces):
        return "Заблокировано ассистентом: изменять можно только файлы в рабочих папках пользователя."
    return None


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    roots = [Path(p) for p in os.environ.get(WORKSPACES_ENV, "").split(os.pathsep) if p]
    reason = check(str(event.get("tool_name", "")), event.get("tool_input") or {}, roots)
    if reason:
        os.write(2, reason.encode("utf-8"))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
