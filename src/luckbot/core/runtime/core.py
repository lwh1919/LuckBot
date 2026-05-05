"""LuckBot 统一运行时内核。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from luckbot.domains.skills.directive import extract_remote_skill_directive
from luckbot.domains.skills.types import RemoteSkillRequest
from luckbot.core.llm.client import build_llm
from luckbot.core.observability import (
    ensure_observability_context,
    increment_counter,
    log_event,
    record_exception,
    record_histogram,
    start_langsmith_run,
    start_span,
    use_observability_context,
)
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.plugin.hooks import (
    AfterLLMCallInput,
    AfterRunInput,
    AfterToolCallInput,
    BeforeLLMCallInput,
    BeforeRunInput,
    BeforeToolCallInput,
)
from luckbot.core.plugin.manager import PluginManager

DEFAULT_MAX_STEPS = 25
logger = logging.getLogger(__name__)


class RuntimeToolProvider(Protocol):
    """轻量工具提供器。"""

    def register_tools(self, ctx: PluginContext) -> None: ...


class RuntimeServiceProvider(Protocol):
    """轻量服务提供器。"""

    def register_services(self, ctx: PluginContext) -> None: ...


@dataclass(slots=True)
class RuntimeProfile:
    """一次运行的能力与生命周期 profile。"""

    plugins: list[LuckbotPlugin] = field(default_factory=list)
    tool_providers: list[RuntimeToolProvider] = field(default_factory=list)
    service_providers: list[RuntimeServiceProvider] = field(default_factory=list)
    enable_run_hooks: bool = True
    normalize_remote_skill_directive: bool = False


@dataclass(slots=True)
class RuntimeSpec:
    """统一 runtime 输入。"""

    system_prompt: str
    max_steps: int
    profile: RuntimeProfile = field(default_factory=RuntimeProfile)
    session_key: str | None = None
    owner_id: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    platform: str | None = None
    gateway_message_id: str | None = None
    gateway_trace_id: str | None = None
    chat_id: str | None = None
    user_id: str | None = None
    plugin_manager: PluginManager | None = None
    user_input: str = ""
    conversation_history: list[Any] = field(default_factory=list)
    messages: list[Any] | None = None
    remote_skill: RemoteSkillRequest | None = None


@dataclass(slots=True)
class RuntimeResult:
    """统一 runtime 输出。"""

    final_text: str
    messages: list[Any]


class _RuntimeProvidersPlugin(LuckbotPlugin):
    """把轻量 providers 适配到现有插件体系。"""

    name = "luckbot/runtime-providers"
    version = "0.1.0"

    def __init__(
        self,
        tool_providers: list[RuntimeToolProvider],
        service_providers: list[RuntimeServiceProvider],
    ) -> None:
        self._tool_providers = list(tool_providers)
        self._service_providers = list(service_providers)

    async def initialize(self, ctx: PluginContext) -> None:
        for provider in self._tool_providers:
            provider.register_tools(ctx)
        for provider in self._service_providers:
            provider.register_services(ctx)


def _merge_remote_skills(
    explicit: RemoteSkillRequest | None,
    inline: RemoteSkillRequest | None,
) -> RemoteSkillRequest | None:
    if explicit is None:
        return inline
    if inline is None:
        return explicit
    if explicit.skill_name != inline.skill_name:
        raise ValueError(
            f"远程 skill 冲突：显式指定为「{explicit.skill_name}」，"
            f"文本指令为「{inline.skill_name}」"
        )
    return explicit


def _validate_runtime_spec(spec: RuntimeSpec) -> None:
    profile = spec.profile
    if spec.plugin_manager is not None and (
        profile.plugins or profile.tool_providers or profile.service_providers
    ):
        raise ValueError("外部 PluginManager 模式下不可再传 plugins 或 providers")

    if spec.messages is not None:
        if profile.enable_run_hooks:
            raise ValueError("messages 模式不支持 before_run / after_run")
        if spec.user_input.strip():
            raise ValueError("messages 模式下不可同时传 user_input")
        if spec.conversation_history:
            raise ValueError("messages 模式下不可同时传 conversation_history")
        if profile.normalize_remote_skill_directive:
            raise ValueError("messages 模式不支持 remote skill 文本规范化")
        if spec.remote_skill is not None:
            raise ValueError("messages 模式不支持 remote_skill")
        return

    if not spec.user_input.strip():
        raise ValueError("user_input 不能为空")


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content or "")


def _serialize_message(message: Any) -> dict[str, Any]:
    role = type(message).__name__
    payload: dict[str, Any] = {
        "role": role,
        "content": _stringify_content(getattr(message, "content", "")),
    }
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = tool_calls
    tool_call_id = getattr(message, "tool_call_id", "")
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    return payload


def _serialize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    return [_serialize_message(message) for message in messages]


async def run_runtime(spec: RuntimeSpec) -> RuntimeResult:
    """统一 runtime 入口。"""
    _validate_runtime_spec(spec)
    profile = spec.profile

    pm_owned = spec.plugin_manager is None
    pm = spec.plugin_manager or PluginManager()
    pm_initialized = spec.plugin_manager is not None
    if pm_owned:
        plugins = list(profile.plugins)
        if profile.tool_providers or profile.service_providers:
            plugins.append(
                _RuntimeProvidersPlugin(
                    tool_providers=profile.tool_providers,
                    service_providers=profile.service_providers,
                )
            )
        await pm.initialize(plugins=plugins)
        pm_initialized = True

    current_tools = dict(pm.get_tools())
    current_prompt = spec.system_prompt
    conv_history: list[Any] = list(spec.conversation_history)
    messages: list[Any] = []
    answer = ""
    run_error: Exception | None = None
    after_run_error: Exception | None = None
    normalized_user_input = spec.user_input
    effective_remote_skill = spec.remote_skill
    root_outputs: dict[str, Any] | None = None
    obs_ctx = ensure_observability_context(
        trace_id=spec.trace_id,
        run_id=spec.run_id,
        session_key=spec.session_key,
        owner_id=spec.owner_id,
        platform=spec.platform,
        gateway_message_id=spec.gateway_message_id,
        gateway_trace_id=spec.gateway_trace_id,
        chat_id=spec.chat_id,
        user_id=spec.user_id,
    )

    with use_observability_context(obs_ctx):
        run_attrs = {
            **obs_ctx.as_attributes(),
            "luckbot.max_steps": spec.max_steps,
            "luckbot.run_mode": "messages" if spec.messages is not None else "user_input",
            "luckbot.enable_run_hooks": profile.enable_run_hooks,
        }
        increment_counter("luckbot_runtime_runs_total", attributes=run_attrs)
        async with start_langsmith_run(
            "luckbot.agent.run",
            run_type="chain",
            inputs={
                "system_prompt": spec.system_prompt,
                "messages": _serialize_messages(spec.messages or spec.conversation_history),
                "user_input": spec.user_input,
            },
            metadata={"max_steps": spec.max_steps},
            run_id=obs_ctx.langsmith_run_id,
        ) as root_run:
            with start_span("agent.run", attributes=run_attrs):
                log_event(
                    logger,
                    logging.INFO,
                    "runtime.run.start",
                    max_steps=spec.max_steps,
                    run_mode=run_attrs["luckbot.run_mode"],
                )
                try:
                    try:
                        if spec.messages is None and profile.normalize_remote_skill_directive:
                            normalized_user_input, inline_remote_skill = extract_remote_skill_directive(
                                spec.user_input
                            )
                            effective_remote_skill = _merge_remote_skills(
                                spec.remote_skill, inline_remote_skill
                            )

                        if profile.enable_run_hooks:
                            for hook in pm.get_hooks("before_run"):
                                result = await hook(
                                    BeforeRunInput(
                                        tools=current_tools,
                                        system_prompt=current_prompt,
                                        conversation_history=conv_history,
                                        session_key=spec.session_key,
                                        owner_id=spec.owner_id,
                                        trace_id=obs_ctx.trace_id,
                                        run_id=obs_ctx.run_id,
                                        remote_skill=effective_remote_skill,
                                    )
                                )
                                if result is not None:
                                    if result.tools is not None:
                                        current_tools = result.tools
                                    if result.system_prompt is not None:
                                        current_prompt = result.system_prompt
                                    if result.conversation_history is not None:
                                        conv_history = list(result.conversation_history)

                        if spec.messages is not None:
                            messages = list(spec.messages)
                        else:
                            messages = list(conv_history)
                            messages.append(HumanMessage(content=normalized_user_input))

                        answer, messages = await run_react_loop(
                            messages=messages,
                            tools=current_tools,
                            system_prompt=current_prompt,
                            plugin_manager=pm,
                            max_steps=spec.max_steps,
                            session_key=spec.session_key,
                            owner_id=spec.owner_id,
                            trace_id=obs_ctx.trace_id,
                            run_id=obs_ctx.run_id,
                        )
                        record_histogram(
                            "luckbot_runtime_message_count",
                            len(messages),
                            attributes=run_attrs,
                        )
                        root_outputs = {
                            "final_text": answer,
                            "message_count": len(messages),
                        }
                    except Exception as exc:
                        run_error = exc
                        record_exception(exc)
                        increment_counter(
                            "luckbot_runtime_errors_total",
                            attributes={**run_attrs, "error_type": exc.__class__.__name__},
                        )
                        root_run.end(error=str(exc))
                    finally:
                        if profile.enable_run_hooks:
                            for hook in pm.get_hooks("after_run"):
                                try:
                                    await hook(
                                        AfterRunInput(
                                            result=answer,
                                            messages=messages,
                                            session_key=spec.session_key,
                                            owner_id=spec.owner_id,
                                            trace_id=obs_ctx.trace_id,
                                            run_id=obs_ctx.run_id,
                                            error=run_error,
                                        )
                                    )
                                except Exception as hook_exc:
                                    logger.exception("after_run hook 执行失败")
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

    return RuntimeResult(final_text=answer, messages=messages)


async def run_react_loop(
    messages: list[Any],
    tools: dict[str, Any],
    system_prompt: str,
    plugin_manager: PluginManager,
    *,
    max_steps: int,
    session_key: str | None = None,
    owner_id: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> tuple[str, list[Any]]:
    """LLM 与工具交替的 ReAct 内循环。"""
    llm = build_llm()
    steps = 0
    obs_ctx = ensure_observability_context(
        trace_id=trace_id,
        run_id=run_id,
        session_key=session_key,
        owner_id=owner_id,
    )

    while steps < max_steps:
        with start_span(
            "agent.step",
            attributes={
                **obs_ctx.as_attributes(),
                "agent.step.index": steps + 1,
            },
        ):
            current_tools = dict(tools)
            current_prompt = system_prompt

            for hook in plugin_manager.get_hooks("before_llm_call"):
                result = await hook(
                    BeforeLLMCallInput(
                        tools=current_tools,
                        system_prompt=current_prompt,
                        messages=messages,
                        session_key=session_key,
                        owner_id=owner_id,
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
                        messages.clear()
                        messages.extend(result.messages)

            tool_list = list(current_tools.values())
            llm_with_tools = llm.bind_tools(tool_list) if tool_list else llm
            full_messages = [SystemMessage(content=current_prompt), *messages]
            llm_attrs = {
                **obs_ctx.as_attributes(),
                "llm.tool_count": len(tool_list),
                "llm.message_count": len(full_messages),
            }
            async with start_langsmith_run(
                "luckbot.llm.call",
                run_type="llm",
                inputs={
                    "system_prompt": current_prompt,
                    "messages": _serialize_messages(messages),
                    "tools": sorted(current_tools.keys()),
                },
                metadata={"step": steps + 1},
            ) as llm_run:
                with start_span("llm.call", attributes=llm_attrs):
                    response = await llm_with_tools.ainvoke(full_messages)
                    llm_run.end(
                        outputs={
                            "content": _stringify_content(response.content),
                            "tool_calls": response.tool_calls,
                            "usage": getattr(response, "usage_metadata", None),
                        }
                    )
                    record_histogram(
                        "luckbot_llm_tool_calls",
                        len(response.tool_calls),
                        attributes=llm_attrs,
                    )
                    messages.append(response)

            for hook in plugin_manager.get_hooks("after_llm_call"):
                await hook(
                    AfterLLMCallInput(
                        response=response,
                        usage=getattr(response, "usage_metadata", None),
                        trace_id=obs_ctx.trace_id,
                        run_id=obs_ctx.run_id,
                    )
                )

            if not response.tool_calls:
                content = response.content or ""
                final_text = content if isinstance(content, str) else str(content)
                return final_text, messages

            pending = []
            for tc in response.tool_calls:
                pending.append(
                    _run_one_tool(
                        tc,
                        current_tools,
                        plugin_manager,
                        trace_id=obs_ctx.trace_id,
                        run_id=obs_ctx.run_id,
                    )
                )
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
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> ToolMessage:
    """执行单个 tool call。"""
    tool_name: str = tc["name"]
    tool_args: dict[str, Any] = tc["args"]
    tool_call_id: str = tc["id"]
    obs_ctx = ensure_observability_context(trace_id=trace_id, run_id=run_id)

    for hook in plugin_manager.get_hooks("before_tool_call"):
        result = await hook(
            BeforeToolCallInput(
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
        "tool.name": tool_name,
        "tool.call_id": tool_call_id,
    }
    async with start_langsmith_run(
        f"tool:{tool_name}",
        run_type="tool",
        inputs={"args": tool_args},
        metadata={"tool_call_id": tool_call_id},
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
