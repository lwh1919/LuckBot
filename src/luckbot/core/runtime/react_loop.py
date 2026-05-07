"""ReAct 内循环：LLM 调用与 tool call 执行。"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import SystemMessage, ToolMessage

from luckbot.core.llm.client import build_llm
from luckbot.core.observability import (
    ensure_observability_context,
    record_exception,
    record_histogram,
    start_langsmith_run,
    start_span,
)
from luckbot.core.plugin.hooks import (
    AfterLLMCallInput,
    AfterToolCallInput,
    BeforeLLMCallInput,
    BeforeToolCallInput,
)
from luckbot.core.plugin.manager import PluginManager
from luckbot.core.runtime.serialization import serialize_messages, stringify_content
from luckbot.core.runtime.types import RuntimeContext


async def run_react_loop(
    ctx: RuntimeContext,
    plugin_manager: PluginManager,
) -> tuple[str, list[Any]]:
    """LLM 与工具交替的 ReAct 内循环。"""
    llm = build_llm()
    steps = 0
    obs_ctx = ensure_observability_context(
        trace_id=ctx.trace_id,
        run_id=ctx.run_id,
    )

    while steps < ctx.max_steps:
        with start_span(
            "agent.step",
            attributes={
                **obs_ctx.as_attributes(),
                **ctx.as_identity_attributes(),
                "agent.step.index": steps + 1,
            },
        ):
            current_tools = dict(ctx.current_tools)
            current_prompt = ctx.current_prompt

            for hook in plugin_manager.get_hooks("before_llm_call"):
                result = await hook(
                    BeforeLLMCallInput(
                        runtime_context=ctx,
                        tools=current_tools,
                        system_prompt=current_prompt,
                        messages=ctx.messages,
                        session_key=ctx.session_key,
                        owner_id=ctx.owner_id,
                        trace_id=obs_ctx.trace_id,
                        run_id=obs_ctx.run_id,
                    )
                )
                if result is not None:
                    if result.tools is not None:
                        current_tools = result.tools
                    if result.system_prompt is not None:
                        current_prompt = result.system_prompt
                    if result.messages is not None:
                        ctx.messages.clear()
                        ctx.messages.extend(result.messages)

            ctx.current_tools = current_tools
            ctx.current_prompt = current_prompt
            tool_list = list(current_tools.values())
            llm_with_tools = llm.bind_tools(tool_list) if tool_list else llm
            full_messages = [SystemMessage(content=current_prompt), *ctx.messages]
            llm_attrs = {
                **obs_ctx.as_attributes(),
                **ctx.as_identity_attributes(),
                "llm.tool_count": len(tool_list),
                "llm.message_count": len(full_messages),
            }
            async with start_langsmith_run(
                "luckbot.llm.call",
                run_type="llm",
                inputs={
                    "system_prompt": current_prompt,
                    "messages": serialize_messages(ctx.messages),
                    "tools": sorted(current_tools.keys()),
                },
                metadata={**ctx.as_identity_metadata(), "step": steps + 1},
                tags=ctx.as_identity_tags(),
            ) as llm_run:
                with start_span("llm.call", attributes=llm_attrs):
                    response = await llm_with_tools.ainvoke(full_messages)
                    llm_run.end(
                        outputs={
                            "content": stringify_content(response.content),
                            "tool_calls": response.tool_calls,
                            "usage": getattr(response, "usage_metadata", None),
                        }
                    )
                    record_histogram(
                        "luckbot_llm_tool_calls",
                        len(response.tool_calls),
                        attributes=llm_attrs,
                    )
                    ctx.messages.append(response)

            for hook in plugin_manager.get_hooks("after_llm_call"):
                await hook(
                    AfterLLMCallInput(
                        runtime_context=ctx,
                        response=response,
                        usage=getattr(response, "usage_metadata", None),
                        trace_id=obs_ctx.trace_id,
                        run_id=obs_ctx.run_id,
                    )
                )

            if not response.tool_calls:
                content = response.content or ""
                final_text = content if isinstance(content, str) else str(content)
                return final_text, ctx.messages

            pending = []
            for tc in response.tool_calls:
                pending.append(
                    _run_one_tool(
                        ctx,
                        tc,
                        current_tools,
                        plugin_manager,
                    )
                )
            tool_messages = await asyncio.gather(*pending, return_exceptions=True)

            n_calls = len(response.tool_calls)
            for i in range(n_calls):
                tc = response.tool_calls[i]
                msg = tool_messages[i]
                if isinstance(msg, Exception):
                    ctx.messages.append(
                        ToolMessage(content=f"工具异常: {msg}", tool_call_id=tc["id"])
                    )
                else:
                    ctx.messages.append(msg)

            steps += 1

    return "已达最大步数，任务可能未完成。", ctx.messages


async def _run_one_tool(
    ctx: RuntimeContext,
    tc: dict[str, Any],
    current_tools: dict[str, Any],
    plugin_manager: PluginManager,
) -> ToolMessage:
    """执行单个 tool call。"""
    tool_name: str = tc["name"]
    tool_args: dict[str, Any] = tc["args"]
    tool_call_id: str = tc["id"]
    obs_ctx = ensure_observability_context(
        trace_id=ctx.trace_id,
        run_id=ctx.run_id,
    )

    for hook in plugin_manager.get_hooks("before_tool_call"):
        result = await hook(
            BeforeToolCallInput(
                runtime_context=ctx,
                name=tool_name,
                args=tool_args,
                tool_call_id=tool_call_id,
                trace_id=obs_ctx.trace_id,
                run_id=obs_ctx.run_id,
            )
        )
        if result is not None and result.args is not None:
            tool_args = result.args

    tool = current_tools.get(tool_name)
    tool_attrs = {
        **obs_ctx.as_attributes(),
        **ctx.as_identity_attributes(),
        "tool.name": tool_name,
        "tool.call_id": tool_call_id,
    }
    async with start_langsmith_run(
        f"tool:{tool_name}",
        run_type="tool",
        inputs={"args": tool_args},
        metadata={**ctx.as_identity_metadata(), "tool_call_id": tool_call_id},
        tags=ctx.as_identity_tags(),
    ) as tool_run:
        with start_span("tool.call", attributes=tool_attrs):
            if tool is None:
                output = f"执行工具 {tool_name} 失败：未找到该工具"
            else:
                try:
                    if hasattr(tool, "ainvoke"):
                        output_obj = await tool.ainvoke(tool_args)
                    else:
                        output_obj = tool.invoke(tool_args)
                    output = output_obj if isinstance(output_obj, str) else str(output_obj)
                except Exception as exc:
                    record_exception(exc)
                    output = f"工具异常: {exc}"
            tool_run.end(outputs={"output": output})
            record_histogram(
                "luckbot_tool_output_chars",
                len(output),
                attributes=tool_attrs,
            )

    for hook in plugin_manager.get_hooks("after_tool_call"):
        result = await hook(
            AfterToolCallInput(
                runtime_context=ctx,
                name=tool_name,
                args=tool_args,
                output=output,
                tool_call_id=tool_call_id,
                trace_id=obs_ctx.trace_id,
                run_id=obs_ctx.run_id,
            )
        )
        if result is not None and result.output is not None:
            output = result.output

    return ToolMessage(content=output, tool_call_id=tool_call_id)
