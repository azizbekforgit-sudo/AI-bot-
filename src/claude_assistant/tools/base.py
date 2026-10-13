from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Outcome of a local tool. `verified` is True only when the effect was actually checked."""

    ok: bool
    message: str
    verified: bool = False
    data: dict[str, Any] = field(default_factory=dict)
