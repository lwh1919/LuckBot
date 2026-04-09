"""Agent 运行入口与 ReAct 内循环。

公共接口 ``agent_loop`` 覆盖完整生命周期：
  before_run → ReAct 循环 → after_run

内循环 ``_react_loop`` 负责 LLM/tool 交替执行：
  before_llm_call / after_llm_call — 每轮 ReAct 迭代
  before_tool_call / after_tool_call — 每次工具调用
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent.llm.client import build_llm
from agent.plugin.hooks import (
    AfterLLMCallInput,
    AfterRunInput,
    AfterToolCallInput,
    BeforeLLMCallInput,
    BeforeRunInput,
    BeforeToolCallInput,
)
from agent.plugin.manager import PluginManager

DEFAULT_MAX_STEPS = 25


async def agent_loop(
    user_input: str,
    plugin_manager: PluginManager,
    *,
    system_prompt: str,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> str:
    """完整 Agent 运行入口。

    任何调用方（CLI / API / 测试）只需调此函数，
    即可获得 before_run → ReAct → after_run 的完整 hook 语义。
    """
    if plugin_manager.get_tools() is not None:
        current_tools: dict[str, Any] = plugin_manager.get_tools()
    else:
        current_tools = {}
    current_prompt = system_prompt

    # ── before_run ──────────────────────────────────────────────
    # 按顺序调用所有 before_run 钩子，并允许它们改 current_tools / current_prompt，改完再进 _react_loop 和 LLM 对话
    for hook in plugin_manager.get_hooks("before_run"):
        result = await hook(
            BeforeRunInput(tools=current_tools, system_prompt=current_prompt)
        )
        if result is not None:
            if result.tools is not None:
                current_tools = result.tools
            if result.system_prompt is not None:
                current_prompt = result.system_prompt

    # ── ReAct 内循环 ────────────────────────────────────────────
    answer, messages = await _react_loop(
        user_input,
        current_tools,
        current_prompt,
        plugin_manager,
        max_steps=max_steps,
    )

    # ── after_run ───────────────────────────────────────────────
    for hook in plugin_manager.get_hooks("after_run"):
        await hook(AfterRunInput(result=answer, messages=messages))

    return answer


# ── 私有：ReAct 内循环 ──────────────────────────────────────────


async def _react_loop(
    user_input: str,
    tools: dict[str, Any],
    system_prompt: str,
    plugin_manager: PluginManager,
    *,
    max_steps: int,
) -> tuple[str, list[Any]]:
    """LLM 推理与工具执行的交替循环。

    返回 ``(最终文本, 完整 messages 列表)``。
    """
    messages: list[Any] = [HumanMessage(content=user_input)]
    llm = build_llm()
    steps = 0

    while steps < max_steps:
        current_tools = dict(tools)
        current_prompt = system_prompt

        # ① before_llm_call
        for hook in plugin_manager.get_hooks("before_llm_call"):
            result = await hook(
                BeforeLLMCallInput(
                    tools=current_tools,
                    system_prompt=current_prompt,
                    messages=messages,
                )
            )
            if result is not None:
                if result.tools is not None:
                    current_tools = result.tools
                if result.system_prompt is not None:
                    current_prompt = result.system_prompt

        # ② 调用 LLM
        tool_list = list(current_tools.values())
        if len(tool_list) > 0:
            llm_with_tools = llm.bind_tools(tool_list)
        else:
            llm_with_tools = llm
        full_messages = [SystemMessage(content=current_prompt), *messages]
        response: AIMessage = await llm_with_tools.ainvoke(full_messages)
        messages.append(response)

        # ③ after_llm_call
        for hook in plugin_manager.get_hooks("after_llm_call"):
            await hook(
                AfterLLMCallInput(
                    response=response,
                    usage=getattr(response, "usage_metadata", None),
                )
            )

        # ④ 无 tool call → 结束（可将 content 视为最终回答）
        if not response.tool_calls:
            content = response.content or ""
            final_text = content if isinstance(content, str) else str(content)
            return final_text, messages

        # ⑤ tool calls 并发执行
        pending = []
        for tc in response.tool_calls:
            pending.append(_run_one_tool(tc, current_tools, plugin_manager))
        tool_messages = await asyncio.gather(*pending, return_exceptions=True)

        n_calls = len(response.tool_calls)
        for i in range(n_calls):
            tc = response.tool_calls[i]
            msg = tool_messages[i]
            if isinstance(msg, Exception):
                messages.append(
                    ToolMessage(content=f"工具异常: {msg}", tool_call_id=tc["id"])
                )
            else:
                messages.append(msg)

        steps += 1

    return "已达最大步数，任务可能未完成。", messages


async def _run_one_tool(
    tc: dict[str, Any],
    current_tools: dict[str, Any],
    plugin_manager: PluginManager,
) -> ToolMessage:
    """执行单个 tool call（含 before/after hook），供 asyncio.gather 并发调用。"""
    tool_name: str = tc["name"]
    tool_args: dict[str, Any] = tc["args"]
    tool_call_id: str = tc["id"]

    for hook in plugin_manager.get_hooks("before_tool_call"):
        result = await hook(BeforeToolCallInput(
            name=tool_name, args=tool_args, tool_call_id=tool_call_id,
        ))
        if result is not None and result.args is not None:
            tool_args = result.args

    tool_fn = current_tools.get(tool_name)
    if tool_fn is None:
        output = f"错误：未知工具「{tool_name}」"
    else:
        try:
            raw = await tool_fn.ainvoke(tool_args)
            output = raw if isinstance(raw, str) else str(raw)
        except Exception as exc:
            output = f"执行工具「{tool_name}」时出错: {exc}"

    for hook in plugin_manager.get_hooks("after_tool_call"):
        result = await hook(
            AfterToolCallInput(
                name=tool_name, args=tool_args, output=output,
                tool_call_id=tool_call_id,
            )
        )
        if result is not None and result.output is not None:
            output = result.output

    return ToolMessage(content=output, tool_call_id=tc["id"])
