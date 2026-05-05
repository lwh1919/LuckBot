"""Session key helpers for local and gateway worlds."""

from __future__ import annotations


def normalize_session_name(value: str | None) -> str:
    return (value or "default").strip() or "default"


def build_local_session_key(value: str | None) -> str:
    return f"local:{normalize_session_name(value)}"


def build_gateway_cli_session_key(value: str | None) -> str:
    return f"gateway:cli:{normalize_session_name(value)}"
