"""平台无关 gateway 类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ReceiveIdType = Literal["open_id", "chat_id"]
ChatType = Literal["dm", "group"]


@dataclass(slots=True)
class OutboundTarget:
    receive_id: str
    receive_id_type: ReceiveIdType
    source_message_id: str | None = None


@dataclass(slots=True)
class IncomingEnvelope:
    platform: str
    chat_type: ChatType
    chat_id: str
    user_id: str
    message_id: str
    text: str
    session_key: str
    owner_id: str
    target: OutboundTarget
    mentions: list[str] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str | None = None
    raw_event: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookParseResult:
    ack_payload: dict[str, Any] = field(default_factory=dict)
    incoming: IncomingEnvelope | None = None


@dataclass(slots=True)
class GatewayRunResult:
    final_text: str
    messages: list[Any]


class PlatformResponder(Protocol):
    async def send_progress(self, text: str) -> None: ...

    async def send_final(self, text: str) -> None: ...

    async def send_error(self, text: str) -> None: ...


class PlatformAdapter(Protocol):
    name: str

    def verify_request(self, headers: dict[str, str], body: bytes) -> bool: ...

    def parse_request(self, headers: dict[str, str], body: bytes) -> WebhookParseResult: ...

    async def create_responder(self, incoming: IncomingEnvelope) -> PlatformResponder: ...


class GatewayRunner(Protocol):
    async def run_turn(self, incoming: IncomingEnvelope) -> GatewayRunResult: ...

