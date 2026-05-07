"""LuckBot 统一运行时编排。"""

from __future__ import annotations

import logging

from luckbot.core.observability import (
    increment_counter,
    log_exception,
    record_exception,
    record_histogram,
    start_langsmith_run,
    start_span,
    use_observability_context,
)
from luckbot.core.plugin.hooks import AfterRunInput, BeforeRunInput
from luckbot.core.plugin.manager import PluginManager
from luckbot.core.runtime.react_loop import run_react_loop
from luckbot.core.runtime.serialization import serialize_messages
from luckbot.core.runtime.types import RuntimeContext, RuntimeResult, use_runtime_context

logger = logging.getLogger(__name__)


def _validate_runtime_context(ctx: RuntimeContext) -> None:
    profile = ctx.profile
    if ctx.plugin_manager is not None and profile.plugins:
        raise ValueError("外部 PluginManager 模式下不可再传 plugins")

    if not ctx.messages:
        raise ValueError("messages 不能为空")


async def run_runtime(ctx: RuntimeContext) -> RuntimeResult:
    """统一 runtime 入口。"""
    _validate_runtime_context(ctx)
    profile = ctx.profile

    pm_owned = ctx.plugin_manager is None
    pm = ctx.plugin_manager or PluginManager()
    pm_initialized = ctx.plugin_manager is not None
    if pm_owned:
        await pm.initialize(plugins=list(profile.plugins))
        pm_initialized = True

    ctx.prepare(pm.get_tools())
    run_error: Exception | None = None
    after_run_error: Exception | None = None
    root_outputs: dict[str, object] | None = None
    obs_ctx = ctx.to_observability_context()
    ctx.sync_observability_context(obs_ctx)

    with use_runtime_context(ctx):
        with use_observability_context(obs_ctx):
            run_attrs = ctx.as_run_attrs(obs_ctx)
            increment_counter("luckbot_runtime_runs_total", attributes=run_attrs)
            async with start_langsmith_run(
                "luckbot.agent.run",
                run_type="chain",
                inputs={
                    "system_prompt": ctx.system_prompt,
                    "messages": serialize_messages(ctx.messages),
                },
                metadata={**ctx.as_identity_metadata(), "max_steps": ctx.max_steps},
                tags=ctx.as_identity_tags(),
                run_id=obs_ctx.langsmith_run_id,
            ) as root_run:
                with start_span("agent.run", attributes=run_attrs):
                    try:
                        try:
                            if profile.enable_run_hooks:
                                for hook in pm.get_hooks("before_run"):
                                    result = await hook(
                                        BeforeRunInput(
                                            runtime_context=ctx,
                                            tools=ctx.current_tools,
                                            system_prompt=ctx.current_prompt,
                                            messages=ctx.messages,
                                            session_key=ctx.session_key,
                                            owner_id=ctx.owner_id,
                                            trace_id=obs_ctx.trace_id,
                                            run_id=obs_ctx.run_id,
                                            requested_skill=ctx.requested_skill,
                                        )
                                    )
                                    if result is not None:
                                        if result.tools is not None:
                                            ctx.current_tools = result.tools
                                        if result.system_prompt is not None:
                                            ctx.current_prompt = result.system_prompt
                                        if result.messages is not None:
                                            ctx.messages = list(result.messages)
                                        if result.session_key is not None:
                                            ctx.session_key = result.session_key
                                        if result.owner_id is not None:
                                            ctx.owner_id = result.owner_id
                            run_attrs = ctx.as_run_attrs(obs_ctx)

                            ctx.final_text, ctx.messages = await run_react_loop(
                                ctx,
                                plugin_manager=pm,
                            )
                            record_histogram(
                                "luckbot_runtime_message_count",
                                len(ctx.messages),
                                attributes=run_attrs,
                            )
                            root_outputs = {
                                "final_text": ctx.final_text,
                                "message_count": len(ctx.messages),
                            }
                        except Exception as exc:
                            run_error = exc
                            ctx.error = exc
                            record_exception(exc)
                            increment_counter(
                                "luckbot_runtime_errors_total",
                                attributes={
                                    **run_attrs,
                                    "error_type": exc.__class__.__name__,
                                },
                            )
                            root_run.end(error=str(exc))
                        finally:
                            if profile.enable_run_hooks:
                                for hook in pm.get_hooks("after_run"):
                                    try:
                                        await hook(
                                            AfterRunInput(
                                                runtime_context=ctx,
                                                result=ctx.final_text,
                                                messages=ctx.messages,
                                                session_key=ctx.session_key,
                                                owner_id=ctx.owner_id,
                                                trace_id=obs_ctx.trace_id,
                                                run_id=obs_ctx.run_id,
                                                error=run_error,
                                            )
                                        )
                                    except Exception as hook_exc:
                                        log_exception(
                                            logger,
                                            "runtime.after_run_hook_failed",
                                            hook_exc,
                                            session_key=ctx.session_key,
                                            owner_id=ctx.owner_id,
                                        )
                                        if run_error is None and after_run_error is None:
                                            after_run_error = hook_exc
                            if run_error is None:
                                if after_run_error is not None:
                                    root_run.end(error=str(after_run_error))
                                else:
                                    root_run.end(outputs=root_outputs)
                    finally:
                        if pm_owned and pm_initialized:
                            await pm.destroy()

    if run_error is not None:
        raise run_error
    if after_run_error is not None:
        raise after_run_error

    return RuntimeResult(final_text=ctx.final_text, messages=ctx.messages)
