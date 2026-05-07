"""平台无关的 webhook 调度器。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from luckbot.core.observability import (
    ensure_observability_context,
    increment_counter,
    log_exception,
    record_exception,
    record_histogram,
    start_span,
    use_observability_context,
)
from luckbot.adapters.gateway.types import GatewayRunner, PlatformAdapter
from luckbot.application.turns import IncomingTurn, TurnResult

logger = logging.getLogger(__name__)


def _gateway_identity_attributes(incoming: IncomingTurn) -> dict[str, object]:
    data = {
        "luckbot.session_key": incoming.session_key,
        "luckbot.owner_id": incoming.owner_id,
    }
    return {key: value for key, value in data.items() if value not in (None, "")}


class SessionBusyError(RuntimeError):
    """Raised when a gateway session is already in progress."""


class GatewayDispatcher:
    """管理 ACK 后的异步执行与同会话并发保护。"""

    def __init__(self, adapter: PlatformAdapter, runner: GatewayRunner) -> None:
        self._adapter = adapter
        self._runner = runner
        self._active_runs: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, incoming: IncomingTurn) -> bool:
        """提交任务；同一 session 已在执行时返回 False。"""
        obs_ctx = ensure_observability_context(
            trace_id=incoming.trace_id,
            platform=incoming.channel,
            gateway_message_id=incoming.message_id,
            gateway_trace_id=incoming.trace_id,
            chat_id=incoming.chat_id,
            user_id=incoming.user_id,
        )
        with use_observability_context(obs_ctx):
            attrs = {
                **obs_ctx.as_attributes(),
                **_gateway_identity_attributes(incoming),
                "gateway.adapter": self._adapter.name,
            }
            with start_span("gateway.enqueue", attributes=attrs):
                if not await self._claim_background(incoming, attrs):
                    responder = await self._adapter.create_responder(incoming)
                    await responder.send_error("上一条消息仍在处理中，请稍后再试。")
                    return False
                return True

    async def run_inline(self, incoming: IncomingTurn) -> TurnResult:
        """同步执行一次 gateway turn，并复用同会话并发保护。"""
        obs_ctx = ensure_observability_context(
            trace_id=incoming.trace_id,
            platform=incoming.channel,
            gateway_message_id=incoming.message_id,
            gateway_trace_id=incoming.trace_id,
            chat_id=incoming.chat_id,
            user_id=incoming.user_id,
        )
        with use_observability_context(obs_ctx):
            attrs = {
                **obs_ctx.as_attributes(),
                **_gateway_identity_attributes(incoming),
                "gateway.adapter": self._adapter.name,
            }
            async with self._lock:
                if incoming.session_key in self._active_runs:
                    increment_counter(
                        "luckbot_gateway_rejected_total",
                        attributes={**attrs, "reason": "active_session"},
                    )
                    raise SessionBusyError("上一条消息仍在处理中，请稍后再试。")
                task = asyncio.current_task()
                if task is None:
                    raise RuntimeError("当前事件循环没有活动任务")
                self._active_runs[incoming.session_key] = task
            try:
                return await self._execute_turn(incoming, attrs)
            finally:
                self._drop_active_run(incoming.session_key, task)

    async def _run(self, incoming: IncomingTurn) -> None:
        obs_ctx = ensure_observability_context(
            trace_id=incoming.trace_id,
            platform=incoming.channel,
            gateway_message_id=incoming.message_id,
            gateway_trace_id=incoming.trace_id,
            chat_id=incoming.chat_id,
            user_id=incoming.user_id,
        )
        with use_observability_context(obs_ctx):
            attrs = {
                **obs_ctx.as_attributes(),
                **_gateway_identity_attributes(incoming),
                "gateway.adapter": self._adapter.name,
            }
            responder = await self._adapter.create_responder(incoming)
            await self._execute_turn(incoming, attrs, responder=responder)

    async def _claim_background(
        self,
        incoming: IncomingTurn,
        attrs: dict[str, object],
    ) -> bool:
        async with self._lock:
            if incoming.session_key in self._active_runs:
                increment_counter(
                    "luckbot_gateway_rejected_total",
                    attributes={**attrs, "reason": "active_session"},
                )
                return False
            increment_counter("luckbot_gateway_enqueued_total", attributes=attrs)
            task = asyncio.create_task(self._run(incoming))
            self._active_runs[incoming.session_key] = task
            task.add_done_callback(
                lambda finished, session_key=incoming.session_key: self._drop_active_run(
                    session_key, finished
                )
            )
            return True

    async def _execute_turn(
        self,
        incoming: IncomingTurn,
        attrs: dict[str, object],
        *,
        responder=None,
    ) -> TurnResult:
        obs_ctx = ensure_observability_context(
            trace_id=incoming.trace_id,
            platform=incoming.channel,
            gateway_message_id=incoming.message_id,
            gateway_trace_id=incoming.trace_id,
            chat_id=incoming.chat_id,
            user_id=incoming.user_id,
        )
        with use_observability_context(obs_ctx):
            with start_span("gateway.run", attributes=attrs):
                started_at = time.monotonic()
                if responder is not None:
                    await responder.send_progress("LuckBot 正在处理中...")
                try:
                    result = await self._runner.run_turn(incoming)
                except Exception as exc:
                    record_exception(exc)
                    increment_counter(
                        "luckbot_gateway_errors_total",
                        attributes={**attrs, "error_type": exc.__class__.__name__},
                    )
                    record_histogram(
                        "luckbot_gateway_run_duration",
                        time.monotonic() - started_at,
                        attributes={**attrs, "outcome": "error"},
                    )
                    log_exception(
                        logger,
                        "gateway.run_failed",
                        exc,
                        session_key=incoming.session_key,
                        owner_id=incoming.owner_id,
                    )
                    if responder is not None:
                        await responder.send_error(str(exc) or exc.__class__.__name__)
                    raise
                increment_counter("luckbot_gateway_runs_total", attributes=attrs)
                record_histogram(
                    "luckbot_gateway_run_duration",
                    time.monotonic() - started_at,
                    attributes={**attrs, "outcome": "success"},
                )
                if responder is not None:
                    await responder.send_final(result.final_text or "任务已完成，但未返回正文。")
                return result

    def _drop_active_run(self, session_key: str, task: asyncio.Task[Any]) -> None:
        current = self._active_runs.get(session_key)
        if current is task:
            self._active_runs.pop(session_key, None)
        if not task.done():
            return
        try:
            task.result()
        except Exception as exc:
            record_exception(exc)
            log_exception(
                logger,
                "gateway.background_task_failed",
                exc,
                session_key=session_key,
            )
