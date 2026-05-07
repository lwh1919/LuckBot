"""LuckBot 统一可观测上下文。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator
from uuid import uuid4


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _new_id(seed: str | None = None) -> str:
    return _clean(seed) or uuid4().hex


@dataclass(slots=True)
class ObservabilityContext:
    """一次 LuckBot 执行链路的标准上下文。"""

    trace_id: str
    run_id: str
    platform: str | None = None
    gateway_message_id: str | None = None
    gateway_trace_id: str | None = None
    chat_id: str | None = None
    user_id: str | None = None
    langsmith_run_id: str | None = None

    @classmethod
    def new(
        cls,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        platform: str | None = None,
        gateway_message_id: str | None = None,
        gateway_trace_id: str | None = None,
        chat_id: str | None = None,
        user_id: str | None = None,
        langsmith_run_id: str | None = None,
    ) -> "ObservabilityContext":
        effective_run_id = _new_id(run_id)
        return cls(
            trace_id=_new_id(trace_id),
            run_id=effective_run_id,
            platform=_clean(platform),
            gateway_message_id=_clean(gateway_message_id),
            gateway_trace_id=_clean(gateway_trace_id),
            chat_id=_clean(chat_id),
            user_id=_clean(user_id),
            langsmith_run_id=_clean(langsmith_run_id) or effective_run_id,
        )

    def derive(self, **updates: Any) -> "ObservabilityContext":
        payload = {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "platform": self.platform,
            "gateway_message_id": self.gateway_message_id,
            "gateway_trace_id": self.gateway_trace_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "langsmith_run_id": self.langsmith_run_id,
        }
        payload.update(updates)
        return ObservabilityContext.new(**payload)

    def as_attributes(self) -> dict[str, Any]:
        data = {
            "luckbot.trace_id": self.trace_id,
            "luckbot.run_id": self.run_id,
            "luckbot.langsmith_run_id": self.langsmith_run_id,
            "luckbot.platform": self.platform,
            "luckbot.gateway_message_id": self.gateway_message_id,
            "luckbot.gateway_trace_id": self.gateway_trace_id,
            "luckbot.chat_id": self.chat_id,
            "luckbot.user_id": self.user_id,
        }
        return {key: value for key, value in data.items() if value not in (None, "")}

    def as_metadata(self) -> dict[str, Any]:
        data = {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "langsmith_run_id": self.langsmith_run_id,
            "platform": self.platform,
            "gateway_message_id": self.gateway_message_id,
            "gateway_trace_id": self.gateway_trace_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
        }
        return {key: value for key, value in data.items() if value not in (None, "")}

    def as_tags(self) -> list[str]:
        tags = ["luckbot"]
        if self.platform:
            tags.append(f"platform:{self.platform}")
        return tags


_OBS_CONTEXT: ContextVar[ObservabilityContext | None] = ContextVar(
    "luckbot_observability_context",
    default=None,
)


def current_observability_context() -> ObservabilityContext | None:
    return _OBS_CONTEXT.get()


def set_observability_context(ctx: ObservabilityContext) -> Token[ObservabilityContext | None]:
    return _OBS_CONTEXT.set(ctx)


def reset_observability_context(token: Token[ObservabilityContext | None]) -> None:
    _OBS_CONTEXT.reset(token)


@contextmanager
def use_observability_context(ctx: ObservabilityContext) -> Iterator[ObservabilityContext]:
    token = set_observability_context(ctx)
    try:
        yield ctx
    finally:
        reset_observability_context(token)


def ensure_observability_context(
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    platform: str | None = None,
    gateway_message_id: str | None = None,
    gateway_trace_id: str | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
    langsmith_run_id: str | None = None,
) -> ObservabilityContext:
    current = current_observability_context()
    if current is None:
        return ObservabilityContext.new(
            trace_id=trace_id,
            run_id=run_id,
            platform=platform,
            gateway_message_id=gateway_message_id,
            gateway_trace_id=gateway_trace_id,
            chat_id=chat_id,
            user_id=user_id,
            langsmith_run_id=langsmith_run_id,
        )
    return current.derive(
        trace_id=trace_id or current.trace_id,
        run_id=run_id or current.run_id,
        platform=platform or current.platform,
        gateway_message_id=gateway_message_id or current.gateway_message_id,
        gateway_trace_id=gateway_trace_id or current.gateway_trace_id,
        chat_id=chat_id or current.chat_id,
        user_id=user_id or current.user_id,
        langsmith_run_id=langsmith_run_id or current.langsmith_run_id,
    )


__all__ = [
    "ObservabilityContext",
    "current_observability_context",
    "ensure_observability_context",
    "reset_observability_context",
    "set_observability_context",
    "use_observability_context",
]
