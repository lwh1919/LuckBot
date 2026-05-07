"""LangSmith 轻量集成。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

from luckbot.core.observability.context import current_observability_context
from luckbot.core.observability.settings import load_observability_settings
from luckbot.core.observability.telemetry import log_exception

try:
    from langsmith import Client
except Exception:  # pragma: no cover - 依赖缺失时退化
    Client = None

logger = logging.getLogger(__name__)

_CURRENT_RUN: ContextVar["LangSmithRunHandle | None"] = ContextVar(
    "luckbot_langsmith_current_run",
    default=None,
)
_CLIENT: Any | None = None


def _client() -> Any | None:
    global _CLIENT
    if Client is None:
        return None
    if _CLIENT is None:
        _CLIENT = Client()
    return _CLIENT


def _ensure_uuid(value: str | UUID | None = None) -> UUID:
    if isinstance(value, UUID):
        return value
    if value:
        try:
            return UUID(str(value))
        except ValueError:
            try:
                return UUID(hex=str(value).replace("-", ""))
            except ValueError:
                pass
    return uuid4()


def _dotted_segment(when: datetime, run_id: UUID) -> str:
    return f"{when.strftime('%Y%m%dT%H%M%S%fZ')}{run_id}"


def _resolve_run_id(
    *,
    parent: "LangSmithRunHandle | None",
    obs_ctx_run_id: str | None,
    requested_run_id: str | None,
) -> UUID:
    if requested_run_id:
        return _ensure_uuid(requested_run_id)
    if parent is None and obs_ctx_run_id:
        return _ensure_uuid(obs_ctx_run_id)
    return uuid4()


@dataclass(slots=True)
class LangSmithRunHandle:
    client: Any | None = None
    run_id: UUID | None = None
    trace_id: UUID | None = None
    dotted_order: str = ""
    token: Token["LangSmithRunHandle | None"] | None = None
    ended: bool = False
    name: str = ""
    run_type: str = ""
    project_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def end(self, *, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
        if self.client is None or self.run_id is None or self.ended:
            return
        self.ended = True
        try:
            self.client.update_run(
                self.run_id,
                outputs=outputs,
                error=error,
                end_time=datetime.now(timezone.utc),
            )
        except Exception as exc:
            log_exception(logger, "langsmith.update_run_failed", exc, name=self.name)


@asynccontextmanager
async def start_langsmith_run(
    name: str,
    *,
    run_type: str,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    run_id: str | None = None,
) -> AsyncIterator[LangSmithRunHandle]:
    settings = load_observability_settings()
    obs_ctx = current_observability_context()
    client = _client()
    if client is None or not settings.langsmith_enabled:
        yield LangSmithRunHandle()
        return

    merged_metadata = dict(obs_ctx.as_metadata()) if obs_ctx is not None else {}
    if metadata:
        merged_metadata.update(metadata)
    merged_tags = list(dict.fromkeys((obs_ctx.as_tags() if obs_ctx is not None else []) + (tags or [])))

    parent = _CURRENT_RUN.get()
    start_time = datetime.now(timezone.utc)
    effective_run_id = _resolve_run_id(
        parent=parent,
        obs_ctx_run_id=obs_ctx.langsmith_run_id if obs_ctx is not None else None,
        requested_run_id=run_id,
    )
    trace_id = parent.trace_id if parent is not None and parent.trace_id is not None else effective_run_id
    dotted_order = _dotted_segment(start_time, effective_run_id)
    if parent is not None and parent.dotted_order:
        dotted_order = f"{parent.dotted_order}.{dotted_order}"

    handle = LangSmithRunHandle(
        client=client,
        run_id=effective_run_id,
        trace_id=trace_id,
        dotted_order=dotted_order,
        name=name,
        run_type=run_type,
        project_name=settings.langsmith_project,
        metadata=merged_metadata,
        tags=merged_tags,
    )

    try:
        client.create_run(
            id=effective_run_id,
            trace_id=trace_id,
            parent_run_id=parent.run_id if parent is not None else None,
            dotted_order=dotted_order,
            name=name,
            run_type=run_type,
            inputs=inputs or {},
            project_name=settings.langsmith_project,
            start_time=start_time,
            extra={"metadata": merged_metadata},
            tags=merged_tags,
        )
    except Exception as exc:
        log_exception(logger, "langsmith.create_run_failed", exc, name=name)
        yield LangSmithRunHandle()
        return

    token = _CURRENT_RUN.set(handle)
    handle.token = token
    pending_exc: BaseException | None = None
    try:
        yield handle
    except BaseException as exc:  # noqa: BLE001
        pending_exc = exc
        if not handle.ended:
            handle.end(error=str(exc))
        raise
    finally:
        if handle.token is not None:
            _CURRENT_RUN.reset(handle.token)
        if pending_exc is None and not handle.ended:
            handle.end()


__all__ = ["LangSmithRunHandle", "start_langsmith_run"]
