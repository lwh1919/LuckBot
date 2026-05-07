"""平台无关 gateway responder / adapter 协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from luckbot.application.turns import IncomingTurn, TurnResult


@dataclass(slots=True)
class WebhookParseResult:
    ack_payload: dict[str, Any] = field(default_factory=dict)
    incoming: IncomingTurn | None = None


class PlatformResponder(Protocol):
    async def send_progress(self, text: str) -> None: ...

    async def send_final(self, text: str) -> None: ...

    async def send_error(self, text: str) -> None: ...


class PlatformAdapter(Protocol):
    name: str

    def verify_request(self, headers: dict[str, str], body: bytes) -> bool: ...

    def parse_request(self, headers: dict[str, str], body: bytes) -> WebhookParseResult: ...

    async def create_responder(self, incoming: IncomingTurn) -> PlatformResponder: ...


class GatewayRunner(Protocol):
    async def run_turn(self, incoming: IncomingTurn) -> TurnResult: ...
