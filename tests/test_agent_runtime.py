from __future__ import annotations

import importlib

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from luckbot.core.observability import current_observability_context
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.plugin.manager import PluginManager
from luckbot.core.runtime import (
    RuntimeProfile,
    RuntimeContext,
    current_runtime_context,
    run_runtime,
)

runtime_module = importlib.import_module("luckbot.core.runtime.react_loop")


class _SequenceLLM:
    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.bound_tools: list[list[object]] = []
        self.invocations: list[list[object]] = []

    def bind_tools(self, tools: list[object]) -> "_SequenceLLM":
        self.bound_tools.append(tools)
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        self.invocations.append(list(messages))
        if not self._responses:
            raise RuntimeError("no more llm responses")
        return self._responses.pop(0)


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


class _PluginEcho(LuckbotPlugin):
    name = "runtime-plugin-echo"

    async def initialize(self, ctx: PluginContext) -> None:
        @tool
        async def runtime_echo(value: str) -> str:
            """回显运行时参数。"""
            return f"runtime:{value}"

        ctx.register_tool("runtime_echo", runtime_echo)


class _RunHookCapture(LuckbotPlugin):
    name = "runtime-run-hook-capture"

    def __init__(self) -> None:
        self.after_run_calls = 0

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_run", self._before_run)
        ctx.register_hook("after_run", self._after_run)

    async def _before_run(self, inp: object):
        from luckbot.core.plugin.hooks import BeforeRunInput, BeforeRunResult

        assert isinstance(inp, BeforeRunInput)
        messages = [HumanMessage(content="prefilled"), *inp.messages]
        return BeforeRunResult(
            system_prompt=inp.system_prompt + "\n\nhooked=yes",
            messages=messages,
        )

    async def _after_run(self, _inp: object) -> None:
        self.after_run_calls += 1


class _IdentityCapture(LuckbotPlugin):
    name = "runtime-identity-capture"

    def __init__(self) -> None:
        self.before_run_identity: tuple[str | None, str | None] | None = None
        self.before_llm_identity: tuple[str | None, str | None] | None = None
        self.after_run_identity: tuple[str | None, str | None] | None = None
        self.before_run_trace: tuple[str | None, str | None] | None = None
        self.before_llm_trace: tuple[str | None, str | None] | None = None
        self.after_run_trace: tuple[str | None, str | None] | None = None

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_run", self._before_run)
        ctx.register_hook("before_llm_call", self._before_llm)
        ctx.register_hook("after_run", self._after_run)

    async def _before_run(self, inp: object):
        from luckbot.core.plugin.hooks import BeforeRunInput

        assert isinstance(inp, BeforeRunInput)
        self.before_run_identity = (inp.session_key, inp.owner_id)
        self.before_run_trace = (inp.trace_id, inp.run_id)
        ctx = current_observability_context()
        assert ctx is not None
        assert ctx.trace_id == inp.trace_id
        return None

    async def _before_llm(self, inp: object):
        from luckbot.core.plugin.hooks import BeforeLLMCallInput

        assert isinstance(inp, BeforeLLMCallInput)
        self.before_llm_identity = (inp.session_key, inp.owner_id)
        self.before_llm_trace = (inp.trace_id, inp.run_id)
        return None

    async def _after_run(self, inp: object) -> None:
        from luckbot.core.plugin.hooks import AfterRunInput

        assert isinstance(inp, AfterRunInput)
        self.after_run_identity = (inp.session_key, inp.owner_id)
        self.after_run_trace = (inp.trace_id, inp.run_id)


class _RuntimeContextIdentityRewrite(LuckbotPlugin):
    name = "runtime-context-identity-rewrite"

    def __init__(self) -> None:
        self.tool_owner: str | None = None
        self.before_llm_owner: str | None = None
        self.after_run_owner: str | None = None

    async def initialize(self, ctx: PluginContext) -> None:
        @tool
        async def runtime_owner() -> str:
            """返回当前 runtime owner。"""
            runtime_ctx = current_runtime_context()
            self.tool_owner = runtime_ctx.owner_id if runtime_ctx is not None else None
            return self.tool_owner or ""

        ctx.register_tool("runtime_owner", runtime_owner)
        ctx.register_hook("before_run", self._before_run)
        ctx.register_hook("before_llm_call", self._before_llm)
        ctx.register_hook("after_run", self._after_run)

    async def _before_run(self, inp: object):
        from luckbot.core.plugin.hooks import BeforeRunInput, BeforeRunResult

        assert isinstance(inp, BeforeRunInput)
        assert inp.runtime_context.owner_id == "owner-before"
        return BeforeRunResult(owner_id="owner-after")

    async def _before_llm(self, inp: object):
        from luckbot.core.plugin.hooks import BeforeLLMCallInput

        assert isinstance(inp, BeforeLLMCallInput)
        self.before_llm_owner = inp.runtime_context.owner_id
        return None

    async def _after_run(self, inp: object) -> None:
        from luckbot.core.plugin.hooks import AfterRunInput

        assert isinstance(inp, AfterRunInput)
        self.after_run_owner = inp.runtime_context.owner_id


@pytest.mark.asyncio
async def test_run_runtime_owned_manager_supports_restricted_messages_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _SequenceLLM(
        [
            _tool_call("runtime_echo", "call-1", value="x"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    result = await run_runtime(
        RuntimeContext(
            system_prompt="runtime",
            max_steps=3,
            profile=RuntimeProfile(
                plugins=[_PluginEcho()],
                enable_run_hooks=False,
            ),
            messages=[HumanMessage(content="hi")],
        )
    )

    assert result.final_text == "done"
    assert any(
        isinstance(msg, ToolMessage) and msg.content == "runtime:x"
        for msg in result.messages
    )
    assert len(llm.bound_tools) == 2


@pytest.mark.asyncio
async def test_run_runtime_external_manager_runs_before_and_after_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _SequenceLLM([AIMessage(content="ok")])
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    capture = _RunHookCapture()
    pm = PluginManager()
    await pm.initialize(plugins=[capture])
    try:
        result = await run_runtime(
            RuntimeContext(
                system_prompt="base",
                max_steps=1,
                profile=RuntimeProfile(enable_run_hooks=True),
                plugin_manager=pm,
                messages=[HumanMessage(content="hello")],
            )
        )
    finally:
        await pm.destroy()

    assert result.final_text == "ok"
    assert capture.after_run_calls == 1
    assert isinstance(result.messages[0], HumanMessage)
    assert result.messages[0].content == "prefilled"
    assert isinstance(result.messages[1], HumanMessage)
    assert result.messages[1].content == "hello"
    assert "hooked=yes" in llm.invocations[0][0].content


@pytest.mark.asyncio
async def test_run_runtime_rejects_external_manager_plus_owned_plugins() -> None:
    pm = PluginManager()
    with pytest.raises(ValueError, match="外部 PluginManager 模式下不可再传 plugins"):
        await run_runtime(
            RuntimeContext(
                system_prompt="x",
                max_steps=1,
                plugin_manager=pm,
                profile=RuntimeProfile(plugins=[_PluginEcho()]),
                messages=[HumanMessage(content="hello")],
            )
        )


@pytest.mark.asyncio
async def test_run_runtime_propagates_session_and_owner_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _SequenceLLM([AIMessage(content="ok")])
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    capture = _IdentityCapture()
    result = await run_runtime(
        RuntimeContext(
            system_prompt="base",
            max_steps=1,
            profile=RuntimeProfile(plugins=[capture]),
            session_key="feishu:user-1",
            owner_id="feishu:owner-1",
            trace_id="trace-gw-1",
            messages=[HumanMessage(content="hello")],
        )
    )

    assert result.final_text == "ok"
    assert capture.before_run_identity == ("feishu:user-1", "feishu:owner-1")
    assert capture.before_llm_identity == ("feishu:user-1", "feishu:owner-1")
    assert capture.after_run_identity == ("feishu:user-1", "feishu:owner-1")
    assert capture.before_run_trace is not None
    assert capture.before_run_trace[0] == "trace-gw-1"
    assert capture.before_llm_trace == capture.before_run_trace
    assert capture.after_run_trace == capture.before_run_trace


@pytest.mark.asyncio
async def test_before_run_owner_rewrite_reaches_runtime_context_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _SequenceLLM(
        [
            _tool_call("runtime_owner", "call-1"),
            AIMessage(content="ok"),
        ]
    )
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    capture = _RuntimeContextIdentityRewrite()
    result = await run_runtime(
        RuntimeContext(
            system_prompt="base",
            max_steps=2,
            profile=RuntimeProfile(plugins=[capture]),
            owner_id="owner-before",
            messages=[HumanMessage(content="hello")],
        )
    )

    assert result.final_text == "ok"
    assert capture.before_llm_owner == "owner-after"
    assert capture.tool_owner == "owner-after"
    assert capture.after_run_owner == "owner-after"
