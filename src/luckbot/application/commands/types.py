from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TokenizedCommand:
    raw_text: str
    name: str
    tokens: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CommandSpec:
    usage: str
    description: str


@dataclass(slots=True)
class CommandContext:
    transport: str
    plugin_manager: Any
    conversation_history: list[Any]
    session_key: str | None = None
    owner_id: str | None = None


@dataclass(slots=True)
class CommandResult:
    handled: bool
    final_text: str = ""
    updated_conversation_history: list[Any] | None = None

