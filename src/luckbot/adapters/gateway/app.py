"""FastAPI gateway entry."""

from __future__ import annotations

import os
import uuid

from luckbot.core.observability import init_observability
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from luckbot.adapters.gateway.feishu.adapter import FeishuAdapter
from luckbot.adapters.gateway.dispatcher import GatewayDispatcher, SessionBusyError
from luckbot.adapters.gateway.service import LuckBotGatewayService
from luckbot.adapters.gateway.types import IncomingEnvelope, OutboundTarget
from luckbot.domains.session import build_gateway_cli_session_key, normalize_session_name


class CliGatewayRequest(BaseModel):
    text: str
    session_key: str | None = None
    owner_id: str | None = None
    trace_id: str | None = None


def create_app():
    app = FastAPI(title="LuckBot Gateway")
    init_observability(component="luckbot", app=app)
    runner = LuckBotGatewayService()
    feishu = FeishuAdapter()
    dispatcher = GatewayDispatcher(feishu, runner)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/feishu/events")
    async def feishu_events(request: Request) -> JSONResponse:
        body = await request.body()
        headers = dict(request.headers.items())
        if not feishu.verify_request(headers, body):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        parsed = feishu.parse_request(headers, body)
        if parsed.incoming is not None:
            await dispatcher.enqueue(parsed.incoming)
        return JSONResponse(parsed.ack_payload or {})

    @app.post("/gateway/cli/turn")
    async def cli_turn(payload: CliGatewayRequest) -> JSONResponse:
        incoming = _build_cli_incoming(payload)
        try:
            result = await dispatcher.run_inline(incoming)
        except SessionBusyError as exc:
            return JSONResponse({"error": "busy", "message": str(exc)}, status_code=409)
        return JSONResponse({"final_text": result.final_text})

    @app.post("/gateway/cli/command")
    async def cli_command(payload: CliGatewayRequest) -> JSONResponse:
        if not payload.text.strip().startswith("/"):
            return JSONResponse(
                {"error": "invalid_command", "message": "CLI command 必须以 / 开头。"},
                status_code=400,
            )
        incoming = _build_cli_incoming(payload)
        try:
            result = await dispatcher.run_inline(incoming)
        except SessionBusyError as exc:
            return JSONResponse({"error": "busy", "message": str(exc)}, status_code=409)
        return JSONResponse({"final_text": result.final_text})

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        "luckbot.adapters.gateway.app:create_app",
        factory=True,
        host=os.getenv("LUCKBOT_GATEWAY_HOST", "0.0.0.0"),
        port=int(os.getenv("LUCKBOT_GATEWAY_PORT", "8000")),
    )


def _build_cli_incoming(payload: CliGatewayRequest) -> IncomingEnvelope:
    raw_session_name = normalize_session_name(payload.session_key)
    owner_id = (payload.owner_id or "local").strip() or "local"
    trace_id = (payload.trace_id or uuid.uuid4().hex).strip() or uuid.uuid4().hex
    return IncomingEnvelope(
        platform="cli",
        chat_type="dm",
        chat_id=raw_session_name,
        user_id=owner_id,
        message_id=f"cli_{trace_id}",
        text=payload.text,
        session_key=build_gateway_cli_session_key(raw_session_name),
        owner_id=owner_id,
        target=OutboundTarget(receive_id=owner_id, receive_id_type="chat_id"),
        trace_id=trace_id,
        raw_event=payload.model_dump(),
    )
