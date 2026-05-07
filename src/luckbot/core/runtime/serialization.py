"""Runtime observability 序列化工具。"""

from __future__ import annotations

from typing import Any


def stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content or "")


def serialize_message(message: Any) -> dict[str, Any]:
    role = type(message).__name__
    payload: dict[str, Any] = {
        "role": role,
        "content": stringify_content(getattr(message, "content", "")),
    }
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = tool_calls
    tool_call_id = getattr(message, "tool_call_id", "")
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    return payload


def serialize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    return [serialize_message(message) for message in messages]
