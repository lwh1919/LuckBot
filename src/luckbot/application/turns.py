"""Unified channel turn types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Channel = Literal["cli", "feishu"]
Transport = Literal["local", "http", "webhook"]
ReceiveIdType = Literal["open_id", "chat_id"]
ChatType = Literal["dm", "group"]


@dataclass(slots=True)
class OutboundTarget:
    receive_id: str
    receive_id_type: ReceiveIdType
    source_message_id: str | None = None


@dataclass(slots=True)
class IncomingTurn:
    channel: Channel
    transport: Transport
    text: str
    session_key: str
    owner_id: str
    chat_type: ChatType = "dm"
    chat_id: str = ""
    user_id: str = ""
    message_id: str = ""
    target: OutboundTarget | None = None
    mentions: list[str] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str | None = None
    raw_event: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TurnResult:
    final_text: str
    messages: list[Any]
    handled_as_command: bool = False
