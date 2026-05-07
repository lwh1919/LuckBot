from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from luckbot.domains.memory.flush_agent import MemoryFlushToolsPlugin, run_memory_flush_agent
from luckbot.plugins.builtin.memory_plugin import MemoryPlugin
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.runtime import RuntimeProfile, RuntimeContext, run_runtime

runtime_module = importlib.import_module("luckbot.core.runtime.react_loop")
flush_agent_module = importlib.import_module("luckbot.domains.memory.flush_agent")
memory_plugin_module = importlib.import_module("luckbot.plugins.builtin.memory_plugin")


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


class _EchoAndAlphaPlugin(LuckbotPlugin):
    name = "echo-and-alpha"

    def __init__(self, *, prefix: str, alpha: str) -> None:
        self._prefix = prefix
        self._alpha = alpha

    async def initialize(self, ctx: PluginContext) -> None:
        prefix = self._prefix

        @tool
        async def plugin_service_echo(value: str) -> str:
            """回显插件参数。"""
            return f"{prefix}:{value}"

        ctx.register_tool("plugin_service_echo", plugin_service_echo)
        ctx.register_service("alpha", self._alpha)


class _FakeEmbeddingProvider:
    model = "fake"
    backend_name = "fake"

    async def embed_query(self, text: str) -> list[float]:  # noqa: ARG002
        return [1.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _text in texts]


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
async def test_run_runtime_messages_mode_runs_with_plugins_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _SequenceLLM(
        [
            _tool_call("plugin_echo", "call-1", value="x"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    result = await run_runtime(
        RuntimeContext(
            system_prompt="plugin-only",
            messages=[HumanMessage(content="hi")],
            max_steps=3,
            profile=RuntimeProfile(
                plugins=[_PluginEcho()],
                enable_run_hooks=False,
            ),
        )
    )

    assert result.final_text == "done"
    assert any(
        isinstance(msg, ToolMessage) and msg.content == "plugin:x"
        for msg in result.messages
    )
    assert len(llm.bound_tools) == 2


@pytest.mark.asyncio
async def test_run_runtime_messages_mode_supports_plugin_tool_and_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _SequenceLLM(
        [
            _tool_call("plugin_service_echo", "call-1", value="y"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    result = await run_runtime(
        RuntimeContext(
            system_prompt="plugin-service",
            messages=[HumanMessage(content="hi")],
            max_steps=3,
            profile=RuntimeProfile(
                plugins=[
                    _EchoAndAlphaPlugin(prefix="plugin", alpha="ok"),
                    _ReadsAlphaService(),
                ],
                enable_run_hooks=False,
            ),
        )
    )

    assert result.final_text == "done"
    assert any(
        isinstance(msg, ToolMessage) and msg.content == "plugin:y"
        for msg in result.messages
    )
    assert "alpha=ok" in llm.invocations[0][0].content


@pytest.mark.asyncio
async def test_run_runtime_messages_mode_destroys_plugins_when_llm_build_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _DestroyFlagPlugin()

    def _raise_build_llm() -> object:
        raise RuntimeError("build-llm boom")

    monkeypatch.setattr(runtime_module, "build_llm", _raise_build_llm)

    with pytest.raises(RuntimeError, match="build-llm boom"):
        await run_runtime(
            RuntimeContext(
                system_prompt="fail",
                messages=[HumanMessage(content="hi")],
                max_steps=1,
                profile=RuntimeProfile(
                    plugins=[plugin],
                    enable_run_hooks=False,
                ),
            )
        )

    assert plugin.destroyed is True


@pytest.mark.asyncio
async def test_run_runtime_messages_mode_memory_plugin_registers_tools_without_run_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_MEMORY_WATCH", "0")
    monkeypatch.setattr(
        memory_plugin_module,
        "build_embedding_provider",
        lambda: _FakeEmbeddingProvider(),
    )
    llm = _SequenceLLM(
        [
            _tool_call("memory_get", "call-1", path="MEMORY.md"),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(runtime_module, "build_llm", lambda: llm)

    result = await run_runtime(
        RuntimeContext(
            system_prompt="memory",
            messages=[HumanMessage(content="read memory")],
            max_steps=3,
            owner_id="owner-a",
            profile=RuntimeProfile(
                plugins=[MemoryPlugin()],
                enable_run_hooks=False,
            ),
        )
    )

    assert result.final_text == "done"
    assert any(
        isinstance(msg, ToolMessage) and '"path": "MEMORY.md"' in msg.content
        for msg in result.messages
    )
    assert len(llm.bound_tools) == 2


class _FakeIndex:
    def __init__(self) -> None:
        self.synced = 0

    async def sync(self) -> None:
        self.synced += 1


@pytest.mark.asyncio
async def test_run_memory_flush_agent_delegates_to_runtime_and_syncs_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[RuntimeContext] = []
    fake_index = _FakeIndex()

    async def _fake_run_runtime(spec: RuntimeContext):
        captured.append(spec)
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

    monkeypatch.setattr(flush_agent_module, "run_runtime", _fake_run_runtime)

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
    spec = captured[0]
    assert spec.max_steps == 4
    assert "memory/2026-05-03.md" in spec.system_prompt
    assert spec.messages is not None
    assert isinstance(spec.messages[0], HumanMessage)
    assert "user: hello" in spec.messages[0].content
    assert spec.profile.enable_run_hooks is False
    assert len(spec.profile.plugins) == 1
    assert isinstance(spec.profile.plugins[0], MemoryFlushToolsPlugin)


@pytest.mark.asyncio
async def test_memory_flush_write_defaults_to_append(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from luckbot.domains.memory.paths import ensure_memory_tree, resolve_memory_paths

    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    paths = resolve_memory_paths()
    ensure_memory_tree(paths)
    target = Path(paths.memory_root) / "memory" / "2026-05-03.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old", encoding="utf-8")

    ctx = PluginContext()
    plugin = MemoryFlushToolsPlugin(
        paths,
        _FakeIndex(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )
    await plugin.initialize(ctx)
    write_tool = ctx.get_tools()["memory_write"]

    await write_tool.ainvoke({"rel_path": "memory/2026-05-03.md", "content": "new"})

    assert target.read_text(encoding="utf-8") == "old\nnew"


@pytest.mark.asyncio
async def test_memory_flush_write_overwrites_when_explicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from luckbot.domains.memory.paths import ensure_memory_tree, resolve_memory_paths

    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    paths = resolve_memory_paths()
    ensure_memory_tree(paths)
    target = Path(paths.memory_root) / "memory" / "2026-05-03.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old", encoding="utf-8")

    ctx = PluginContext()
    plugin = MemoryFlushToolsPlugin(
        paths,
        _FakeIndex(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )
    await plugin.initialize(ctx)
    write_tool = ctx.get_tools()["memory_write"]

    await write_tool.ainvoke(
        {
            "rel_path": "memory/2026-05-03.md",
            "content": "new",
            "mode": "overwrite",
        }
    )

    assert target.read_text(encoding="utf-8") == "new"
