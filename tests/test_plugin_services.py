from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from luckbot.domains.skills.state import SkillStateStore
from luckbot.domains.skills.types import SkillActivationRequest
from luckbot.domains.skills.directive import parse_skill_activation_directive
from luckbot.plugins.builtin.mcp_plugin import MCPPlugin
from luckbot.plugins.builtin.memory_plugin import MemoryPlugin
from luckbot.plugins.builtin.skill_activation_plugin import SkillActivationPlugin
from luckbot.plugins.builtin.session_plugin import SessionPlugin
from luckbot.plugins.builtin.skills_plugin import SkillsPlugin
from luckbot.domains.mcp.config import read_mcp_config, resolve_mcp_config_path
from luckbot.domains.mcp.loader import (
    build_mcp_tools,
    close_mcp_client,
    silence_stdio_server_stderr,
)
from luckbot.plugins.builtin.tools_plugin import ToolsPlugin
from luckbot.core.runtime import RuntimeProfile, RuntimeContext, run_runtime
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.plugin.hooks import AfterRunInput, BeforeLLMCallInput, BeforeRunInput
from luckbot.core.plugin.manager import PluginManager

runtime_module = importlib.import_module("luckbot.core.runtime.react_loop")


def _runtime_context(
    *,
    system_prompt: str = "base",
    messages: list[object] | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        system_prompt=system_prompt,
        messages=list(messages or []),
        max_steps=1,
    )


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (
            f"---\n"
            f"name: {name}\n"
            f"description: remote test skill\n"
            f"when_to_use: test remote preload\n"
            f"---\n\n"
            f"# {name}\n\n"
            f"Use this skill for remote runs.\n"
        ),
        encoding="utf-8",
    )
    (skill_dir / "guide.md").write_text("# guide\n\nGuide body.\n", encoding="utf-8")


class _RegistersAlphaService(LuckbotPlugin):
    name = "alpha-service-plugin"

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_service("alpha", object())


class _RegistersDuplicateAlphaService(LuckbotPlugin):
    name = "duplicate-alpha-service-plugin"

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_service("alpha", object())


class _RegistersRuntimeArtifacts(LuckbotPlugin):
    name = "runtime-artifacts-plugin"

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_service("alpha", "ok")
        ctx.register_tool("echo", object())
        ctx.register_hook("before_run", self._before_run)

    async def _before_run(self, *_args, **_kwargs) -> None:
        return None


class _RaisesBeforeRun(LuckbotPlugin):
    name = "raises-before-run"

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_run", self._before_run)

    async def _before_run(self, *_args, **_kwargs) -> None:
        raise RuntimeError("before-run boom")


class _RaisesBeforeLLM(LuckbotPlugin):
    name = "raises-before-llm"

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_llm_call", self._before_llm)

    async def _before_llm(self, *_args, **_kwargs) -> None:
        raise RuntimeError("before-llm boom")


class _CapturesAfterRun(LuckbotPlugin):
    name = "captures-after-run"

    def __init__(self) -> None:
        self.calls: list[AfterRunInput] = []

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("after_run", self._after_run)

    async def _after_run(self, inp: AfterRunInput) -> None:
        self.calls.append(inp)


def test_plugin_context_rejects_duplicate_service_name() -> None:
    ctx = PluginContext()
    ctx.register_service("alpha", object())

    with pytest.raises(RuntimeError, match="插件服务重名: alpha"):
        ctx.register_service("alpha", object())


def test_plugin_context_keeps_hook_registration_order_by_default() -> None:
    ctx = PluginContext()
    calls: list[str] = []

    def first() -> None:
        calls.append("first")

    def second() -> None:
        calls.append("second")

    ctx.register_hook("before_run", first)
    ctx.register_hook("before_run", second)

    hooks = ctx.get_hooks("before_run")
    assert hooks == [first, second]

    for hook in hooks:
        hook()
    assert calls == ["first", "second"]


def test_plugin_context_orders_hooks_by_priority() -> None:
    ctx = PluginContext()

    def low() -> None:
        pass

    def high() -> None:
        pass

    def middle() -> None:
        pass

    ctx.register_hook("before_run", low, priority=300)
    ctx.register_hook("before_run", high, priority=100)
    ctx.register_hook("before_run", middle, priority=200)

    assert ctx.get_hooks("before_run") == [high, middle, low]


def test_plugin_context_keeps_registration_order_for_same_priority() -> None:
    ctx = PluginContext()

    def first() -> None:
        pass

    def second() -> None:
        pass

    ctx.register_hook("before_run", first, priority=100)
    ctx.register_hook("before_run", second, priority=100)

    assert ctx.get_hooks("before_run") == [first, second]


def test_plugin_context_rejects_unknown_hook_name() -> None:
    ctx = PluginContext()

    with pytest.raises(ValueError, match="未知的 hook"):
        ctx.register_hook("unknown", lambda: None)


@pytest.mark.asyncio
async def test_plugin_manager_initialize_rejects_duplicate_service_from_plugins() -> None:
    pm = PluginManager()

    with pytest.raises(RuntimeError, match="插件服务重名: alpha"):
        await pm.initialize(
            plugins=[_RegistersAlphaService(), _RegistersDuplicateAlphaService()]
        )


@pytest.mark.asyncio
async def test_plugin_manager_destroy_clears_runtime_registries() -> None:
    pm = PluginManager()
    await pm.initialize(plugins=[_RegistersRuntimeArtifacts()])

    assert pm.get_service("alpha") == "ok"
    assert "echo" in pm.get_tools()
    assert len(pm.get_hooks("before_run")) == 1

    await pm.destroy()

    assert pm.get_service("alpha") is None
    assert pm.get_tools() == {}
    assert pm.get_hooks("before_run") == []


@pytest.mark.asyncio
async def test_plugin_manager_can_reinitialize_after_destroy() -> None:
    pm = PluginManager()

    await pm.initialize(plugins=[_RegistersRuntimeArtifacts()])
    assert pm.get_service("alpha") == "ok"

    await pm.destroy()
    assert pm.get_service("alpha") is None

    await pm.initialize(plugins=[_RegistersRuntimeArtifacts()])
    assert pm.get_service("alpha") == "ok"
    assert "echo" in pm.get_tools()
    assert len(pm.get_hooks("before_run")) == 1


@pytest.mark.asyncio
async def test_plugin_manager_rejects_initialize_while_active() -> None:
    pm = PluginManager()
    await pm.initialize(plugins=[_RegistersRuntimeArtifacts()])

    with pytest.raises(RuntimeError, match="请先调用 destroy"):
        await pm.initialize(plugins=[_RegistersRuntimeArtifacts()])


def test_builtin_plugin_dependencies_make_runtime_order_explicit() -> None:
    plugins = [
        SessionPlugin(),
        SkillActivationPlugin(),
        SkillsPlugin(),
        MemoryPlugin(),
        MCPPlugin(),
        ToolsPlugin(),
    ]

    sorted_plugins = PluginManager._topo_sort(plugins)
    names = [p.name for p in sorted_plugins]

    assert names.index("memory") < names.index("session")
    assert names.index("luckbot/built-in-skills") < names.index(
        "luckbot/built-in-skill-activation"
    )
    assert names.index("memory") < names.index("session")


@pytest.mark.asyncio
async def test_mcp_plugin_registers_command_query_services(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text('{"servers":{}}', encoding="utf-8")

    plugin = MCPPlugin(config_path=str(config_path))
    ctx = PluginContext()
    await plugin.initialize(ctx)

    config_snapshot = ctx.get_service("mcp_config_snapshot")
    tool_names = ctx.get_service("mcp_tool_names")

    assert callable(config_snapshot)
    assert callable(tool_names)
    assert config_snapshot().exists is True
    assert await tool_names() == []

    await plugin.destroy(ctx)


@pytest.mark.asyncio
async def test_runtime_runs_after_run_even_when_before_run_fails() -> None:
    capture = _CapturesAfterRun()
    pm = PluginManager()
    await pm.initialize(plugins=[_RaisesBeforeRun(), capture])

    with pytest.raises(RuntimeError, match="before-run boom"):
        await run_runtime(
            RuntimeContext(
                system_prompt="base",
                max_steps=1,
                profile=RuntimeProfile(enable_run_hooks=True),
                plugin_manager=pm,
                messages=[HumanMessage(content="hello")],
            )
        )

    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call.result == ""
    assert len(call.messages) == 1
    assert isinstance(call.messages[0], HumanMessage)
    assert call.messages[0].content == "hello"
    assert isinstance(call.error, RuntimeError)
    assert str(call.error) == "before-run boom"


@pytest.mark.asyncio
async def test_runtime_runs_after_run_even_when_build_llm_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CapturesAfterRun()
    pm = PluginManager()
    await pm.initialize(plugins=[capture])

    def _raise_build_llm() -> object:
        raise RuntimeError("build-llm boom")

    monkeypatch.setattr(runtime_module, "build_llm", _raise_build_llm)

    with pytest.raises(RuntimeError, match="build-llm boom"):
        await run_runtime(
            RuntimeContext(
                system_prompt="base",
                max_steps=1,
                profile=RuntimeProfile(enable_run_hooks=True),
                plugin_manager=pm,
                messages=[HumanMessage(content="hello")],
            )
        )

    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call.result == ""
    assert len(call.messages) == 1
    assert isinstance(call.messages[0], HumanMessage)
    assert call.messages[0].content == "hello"
    assert isinstance(call.error, RuntimeError)
    assert str(call.error) == "build-llm boom"


@pytest.mark.asyncio
async def test_runtime_runs_after_run_even_when_before_llm_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CapturesAfterRun()
    pm = PluginManager()
    await pm.initialize(plugins=[_RaisesBeforeLLM(), capture])

    class _FakeLLM:
        def bind_tools(self, _tools: object) -> "_FakeLLM":
            return self

        async def ainvoke(self, _messages: object) -> AIMessage:
            return AIMessage(content="ok")

    monkeypatch.setattr(runtime_module, "build_llm", lambda: _FakeLLM())

    with pytest.raises(RuntimeError, match="before-llm boom"):
        await run_runtime(
            RuntimeContext(
                system_prompt="base",
                max_steps=1,
                profile=RuntimeProfile(enable_run_hooks=True),
                plugin_manager=pm,
                messages=[HumanMessage(content="hello")],
            )
        )

    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call.result == ""
    assert len(call.messages) == 1
    assert isinstance(call.messages[0], HumanMessage)
    assert call.messages[0].content == "hello"
    assert isinstance(call.error, RuntimeError)
    assert str(call.error) == "before-llm boom"


@pytest.mark.asyncio
async def test_skill_activation_chain_keeps_working_with_remaining_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "activation-demo")
    monkeypatch.setenv("LUCKBOT_SKILLS_DIR", str(skills_root))

    ctx = PluginContext()
    skills = SkillsPlugin()
    activation = SkillActivationPlugin()
    await skills.initialize(ctx)
    await activation.initialize(ctx)

    assert ctx.get_service("skill_registry") is not None
    assert isinstance(ctx.get_service("skill_state_store"), SkillStateStore)
    assert ctx.get_service("skill_runtime") is None

    await skills._before_run(
        BeforeRunInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
        )
    )
    await activation._before_run(
        BeforeRunInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
            requested_skill=SkillActivationRequest(
                skill_name="activation-demo", docs=["guide.md"]
            ),
        )
    )

    result = await skills._before_llm_call(
        BeforeLLMCallInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
        )
    )
    assert result is not None
    assert "Use this skill for remote runs." in result.system_prompt
    assert "Guide body." in result.system_prompt


def test_parse_skill_activation_directive_extracts_inline_skill() -> None:
    text, request = parse_skill_activation_directive("[use skill](demo) hello")

    assert text == "hello"
    assert request == SkillActivationRequest(skill_name="demo")


@pytest.mark.asyncio
async def test_session_after_run_still_calls_memory_sync_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_SESSION", "service-test")
    monkeypatch.setenv("LUCKBOT_SESSION_PERSIST", "1")

    sync_calls: list[str] = []

    async def _fake_sync() -> None:
        sync_calls.append("sync")

    ctx = PluginContext()
    ctx.register_service("memory_sync_now", _fake_sync)

    plugin = SessionPlugin()
    await plugin.initialize(ctx)
    await plugin._after_run(
        AfterRunInput(
            runtime_context=_runtime_context(
                messages=[HumanMessage(content="u"), AIMessage(content="a")]
            ),
            result="ok",
            messages=[HumanMessage(content="u"), AIMessage(content="a")],
        )
    )

    assert sync_calls == ["sync"]


@pytest.mark.asyncio
async def test_mcp_plugin_closes_previous_clients_on_reload_and_destroy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        '{"servers":{"one":{"transport":"http","url":"https://example.com/one"}}}',
        encoding="utf-8",
    )

    closed: list[str] = []
    created: list[str] = []

    class _FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    class _FakeClient:
        def __init__(self, spec: dict[str, object]) -> None:
            self._name = next(iter(spec.keys()))
            created.append(self._name)

        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool("echo")]

        async def aclose(self) -> None:
            closed.append(self._name)

    import sys

    fake_module = SimpleNamespace(MultiServerMCPClient=_FakeClient)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_module)

    plugin = MCPPlugin(config_path=str(config_path))
    ctx = PluginContext()
    await plugin.initialize(ctx)

    first = await plugin._before_run(
        BeforeRunInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
        )
    )
    assert first is not None
    assert "mcp_one_echo" in (first.tools or {})
    assert created == ["one"]

    config_path.write_text(
        '{"servers":{"two":{"transport":"http","url":"https://example.com/two"}}}',
        encoding="utf-8",
    )
    second = await plugin._before_run(
        BeforeRunInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
        )
    )
    assert second is not None
    assert "mcp_two_echo" in (second.tools or {})
    assert closed == ["one"]
    assert created == ["one", "two"]

    await plugin.destroy(ctx)
    assert closed == ["one", "two"]


@pytest.mark.asyncio
async def test_mcp_plugin_always_refresh_rebuilds_even_without_mtime_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_MCP_REFRESH_POLICY", "always")
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        '{"servers":{"one":{"transport":"http","url":"https://example.com/one"}}}',
        encoding="utf-8",
    )

    closed: list[str] = []
    created: list[str] = []

    class _FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    class _FakeClient:
        def __init__(self, spec: dict[str, object]) -> None:
            self._name = next(iter(spec.keys()))
            created.append(self._name)

        async def get_tools(self) -> list[_FakeTool]:
            return [_FakeTool("echo")]

        async def aclose(self) -> None:
            closed.append(self._name)

    import sys

    fake_module = SimpleNamespace(MultiServerMCPClient=_FakeClient)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_module)

    plugin = MCPPlugin(config_path=str(config_path))
    ctx = PluginContext()
    await plugin.initialize(ctx)

    first = await plugin._before_run(
        BeforeRunInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
        )
    )
    second = await plugin._before_run(
        BeforeRunInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
        )
    )

    assert first is not None
    assert second is not None
    assert created == ["one", "one"]
    assert closed == ["one"]


@pytest.mark.asyncio
async def test_mcp_plugin_caches_empty_tool_result_when_config_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        '{"servers":{"one":{"transport":"http","url":"https://example.com/one"}}}',
        encoding="utf-8",
    )

    closed: list[str] = []
    created: list[str] = []

    class _FakeClient:
        def __init__(self, spec: dict[str, object]) -> None:
            self._name = next(iter(spec.keys()))
            created.append(self._name)

        async def get_tools(self) -> list[object]:
            return []

        async def aclose(self) -> None:
            closed.append(self._name)

    import sys

    fake_module = SimpleNamespace(MultiServerMCPClient=_FakeClient)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_module)

    plugin = MCPPlugin(config_path=str(config_path))
    ctx = PluginContext()
    await plugin.initialize(ctx)

    first = await plugin._before_run(
        BeforeRunInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
        )
    )
    second = await plugin._before_run(
        BeforeRunInput(
            runtime_context=_runtime_context(),
            tools={},
            system_prompt="base",
            messages=[],
        )
    )

    assert first is None
    assert second is None
    assert created == ["one"]
    assert closed == []

    await plugin.destroy(ctx)
    assert closed == ["one"]


@pytest.mark.asyncio
async def test_mcp_loader_closes_failed_client_and_keeps_other_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class _FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    class _FakeClient:
        def __init__(self, spec: dict[str, object]) -> None:
            self._name = next(iter(spec.keys()))

        async def get_tools(self) -> list[_FakeTool]:
            if self._name == "bad":
                raise RuntimeError("boom")
            return [_FakeTool("echo")]

        async def aclose(self) -> None:
            closed.append(self._name)

    import sys

    fake_module = SimpleNamespace(MultiServerMCPClient=_FakeClient)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_module)

    result = await build_mcp_tools(
        {
            "bad": {"transport": "http", "url": "https://example.com/bad"},
            "good": {"transport": "http", "url": "https://example.com/good"},
        }
    )

    assert sorted(result.tools) == ["mcp_good_echo"]
    assert closed == ["bad"]

    for client in result.clients:
        await close_mcp_client(client)
    assert closed == ["bad", "good"]


@pytest.mark.asyncio
async def test_mcp_stdio_stderr_is_silenced(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    loader_module = importlib.import_module("luckbot.domains.mcp.loader")
    captured_errlogs: list[object] = []

    @asynccontextmanager
    async def fake_stdio_client(server: object, errlog: object = sys.stderr):
        captured_errlogs.append(errlog)
        yield ("read", "write")

    fake_sessions = SimpleNamespace(stdio_client=fake_stdio_client)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.sessions", fake_sessions)
    monkeypatch.setattr(loader_module, "_STDIO_STDERR_SILENCED", False)
    monkeypatch.setattr(loader_module, "_STDIO_ERRLOG", None)

    silence_stdio_server_stderr()

    async with fake_sessions.stdio_client("server", errlog=sys.stderr):
        pass

    assert captured_errlogs
    assert captured_errlogs[0] is not sys.stderr
    assert getattr(fake_sessions.stdio_client, "_luckbot_stderr_silenced") is True


def test_read_mcp_config_resolves_project_relative_path_independent_of_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / ".luckbot" / "mcp.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{"servers":{"one":{"transport":"http","url":"https://example.com/one"}}}',
        encoding="utf-8",
    )

    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path.parent)

    snapshot = read_mcp_config()

    assert snapshot.exists is True
    assert snapshot.path == resolve_mcp_config_path()
    assert snapshot.path == str(config_path.resolve())
    assert snapshot.servers == {
        "one": {"transport": "http", "url": "https://example.com/one"}
    }
