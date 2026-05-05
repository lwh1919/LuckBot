from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from luckbot.domains.memory.flush_agent import MemoryFlushToolsPlugin, run_memory_flush_agent
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.subagent import (
    SubagentRunRequest,
    run_subagent,
)

runtime_module = importlib.import_module("luckbot.core.runtime.core")
flush_agent_module = importlib.import_module("luckbot.domains.memory.flush_agent")


class _SequenceLLM:
    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.bound_tools: list[object] = []
        self.invocations: list[list[object]] = []

    def bind_tools(self, tools: list[object]) -> "_SequenceLLM":
        self.bound_tools.append(tools)
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        self.invocations.append(list(messages))
        if not self._responses:
            raise RuntimeError("no more llm responses")
        return self._responses.pop(0)


class _PluginEcho(LuckbotPlugin):
    name = "plugin-echo"

    async def initialize(self, ctx: PluginContext) -> None:
        @tool
        async def plugin_echo(value: str) -> str:
            """回显插件参数。"""
            return f"plugin:{value}"

        ctx.register_tool("plugin_echo", plugin_echo)


class _ReadsAlphaService(LuckbotPlugin):
    name = "reads-alpha-service"

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None

    async def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        ctx.register_hook("before_llm_call", self._before_llm_call)

    async def _before_llm_call(self, inp: object):
        from luckbot.core.plugin.hooks import BeforeLLMCallInput, BeforeLLMCallResult

        assert isinstance(inp, BeforeLLMCallInput)
        alpha = self._ctx.get_service("alpha") if self._ctx else None
        suffix = f"\n\nalpha={alpha}" if alpha is not None else ""
        return BeforeLLMCallResult(system_prompt=inp.system_prompt + suffix)


class _DestroyFlagPlugin(LuckbotPlugin):
    name = "destroy-flag-plugin"

    def __init__(self) -> None:
        self.destroyed = False

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_llm_call", self._before_llm_call)

    async def destroy(self, ctx: PluginContext) -> None:  # noqa: ARG002
        self.destroyed = True

    async def _before_llm_call(self, _inp: object):
        return None


@dataclass
class _ProviderEcho:
    prefix: str = "provider"

    def register_tools(self, ctx: PluginContext) -> None:
        @tool
        async def provider_echo(value: str) -> str:
            """回显 provider 参数。"""
            return f"{self.prefix}:{value}"

        ctx.register_tool("provider_echo", provider_echo)


@dataclass
class _AlphaService:
    value: str

    def register_services(self, ctx: PluginContext) -> None:
        ctx.register_service("alpha", self.value)


def _tool_call(name: str, tool_id: str, **args: object) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": tool_id,
                "type": "tool_call",
            }
        ],
    )


@pytest.mark.asyncio
async def test_run_subagent_runs_with_plugins_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _SequenceLLM(
        [
            _tool_call("plugin_echo", "call-1", value="x"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    result = await run_subagent(
        SubagentRunRequest(
            system_prompt="plugin-only",
            messages=[HumanMessage(content="hi")],
            max_steps=3,
            plugins=[_PluginEcho()],
        )
    )

    assert result.final_text == "done"
    assert any(
        isinstance(msg, ToolMessage) and msg.content == "plugin:x"
        for msg in result.messages
    )
    assert len(llm.bound_tools) == 2


@pytest.mark.asyncio
async def test_run_subagent_supports_tool_and_service_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _SequenceLLM(
        [
            _tool_call("provider_echo", "call-1", value="y"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    result = await run_subagent(
        SubagentRunRequest(
            system_prompt="provider-only",
            messages=[HumanMessage(content="hi")],
            max_steps=3,
            plugins=[_ReadsAlphaService()],
            tool_providers=[_ProviderEcho(prefix="provider")],
            service_providers=[_AlphaService(value="ok")],
        )
    )

    assert result.final_text == "done"
    assert any(
        isinstance(msg, ToolMessage) and msg.content == "provider:y"
        for msg in result.messages
    )
    assert "alpha=ok" in llm.invocations[0][0].content


@pytest.mark.asyncio
async def test_run_subagent_destroys_plugins_when_llm_build_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _DestroyFlagPlugin()

    def _raise_build_llm() -> object:
        raise RuntimeError("build-llm boom")

    monkeypatch.setattr(runtime_module, "build_llm", _raise_build_llm)

    with pytest.raises(RuntimeError, match="build-llm boom"):
        await run_subagent(
            SubagentRunRequest(
                system_prompt="fail",
                messages=[HumanMessage(content="hi")],
                max_steps=1,
                plugins=[plugin],
            )
        )

    assert plugin.destroyed is True


class _FakeIndex:
    def __init__(self) -> None:
        self.synced = 0

    async def sync(self) -> None:
        self.synced += 1


@pytest.mark.asyncio
async def test_run_memory_flush_agent_delegates_to_subagent_runtime_and_syncs_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[SubagentRunRequest] = []
    fake_index = _FakeIndex()

    async def _fake_run_subagent(request: SubagentRunRequest):
        captured.append(request)
        return type(
            "_Result",
            (),
            {
                "final_text": "flush-ok",
                "messages": [
                    HumanMessage(content="input"),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "memory_write",
                                "args": {
                                    "rel_path": "memory/2026-05-03.md",
                                    "content": "saved",
                                    "mode": "append",
                                },
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    ToolMessage(
                        content="[OK] 已写入 memory/2026-05-03.md",
                        tool_call_id="call-1",
                    ),
                ],
            },
        )()

    monkeypatch.setattr(flush_agent_module, "run_subagent", _fake_run_subagent)

    from luckbot.domains.memory.paths import ensure_memory_tree, resolve_memory_paths

    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    paths = resolve_memory_paths()
    ensure_memory_tree(paths)

    final_text, messages = await run_memory_flush_agent(
        "user: hello",
        paths,
        fake_index,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        max_steps=4,
        day="2026-05-03",
    )

    assert final_text == "flush-ok"
    assert len(messages) == 3
    assert fake_index.synced == 1
    assert len(captured) == 1
    request = captured[0]
    assert request.max_steps == 4
    assert "memory/2026-05-03.md" in request.system_prompt
    assert isinstance(request.messages[0], HumanMessage)
    assert "user: hello" in request.messages[0].content
    assert len(request.plugins) == 1
    assert isinstance(request.plugins[0], MemoryFlushToolsPlugin)
