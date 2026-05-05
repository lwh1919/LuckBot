"""subagent 薄适配层：委托统一 runtime core。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from luckbot.core.observability import current_observability_context
from luckbot.core.plugin.base import LuckbotPlugin
from luckbot.core.runtime import (
    RuntimeProfile,
    RuntimeResult,
    RuntimeServiceProvider,
    RuntimeSpec,
    RuntimeToolProvider,
    run_runtime,
)

SubagentToolProvider = RuntimeToolProvider
SubagentServiceProvider = RuntimeServiceProvider


@dataclass(slots=True)
class SubagentRunRequest:
    """一次 subagent 执行请求。"""

    system_prompt: str
    messages: list[Any]
    max_steps: int
    session_key: str | None = None
    plugins: list[LuckbotPlugin] = field(default_factory=list)
    tool_providers: list[SubagentToolProvider] = field(default_factory=list)
    service_providers: list[SubagentServiceProvider] = field(default_factory=list)


@dataclass(slots=True)
class SubagentRunResult:
    """一次 subagent 执行结果。"""

    final_text: str
    messages: list[Any]


def _to_subagent_result(result: RuntimeResult) -> SubagentRunResult:
    return SubagentRunResult(final_text=result.final_text, messages=result.messages)


async def run_subagent(request: SubagentRunRequest) -> SubagentRunResult:
    """受限 profile 适配层。"""
    parent_ctx = current_observability_context()
    result = await run_runtime(
        RuntimeSpec(
            system_prompt=request.system_prompt,
            max_steps=request.max_steps,
            profile=RuntimeProfile(
                plugins=list(request.plugins),
                tool_providers=list(request.tool_providers),
                service_providers=list(request.service_providers),
                enable_run_hooks=False,
                normalize_remote_skill_directive=False,
            ),
            session_key=request.session_key,
            trace_id=parent_ctx.trace_id if parent_ctx is not None else None,
            messages=list(request.messages),
        )
    )
    return _to_subagent_result(result)
