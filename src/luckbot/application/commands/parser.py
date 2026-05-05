from __future__ import annotations

import shlex

from .types import TokenizedCommand


def tokenize_command(text: str) -> TokenizedCommand | None:
    raw = text.strip()
    if not raw.startswith("/"):
        return None
    body = raw[1:].strip()
    if not body:
        return TokenizedCommand(raw_text=raw, name="", tokens=[])
    parts = shlex.split(body)
    if not parts:
        return TokenizedCommand(raw_text=raw, name="", tokens=[])
    return TokenizedCommand(
        raw_text=raw,
        name=parts[0].lower(),
        tokens=parts[1:],
    )

