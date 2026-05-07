from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from luckbot.domains.session.state import resolve_session
from luckbot.domains.session import build_gateway_cli_session_key, build_local_session_key
from luckbot.domains.session.transcript import append_transcript_lines, messages_to_jsonl_lines
from luckbot.application.commands import CommandContext, execute_command, render_help_text, tokenize_command
from luckbot.application.turn_runner import AgentTurnRunner
from luckbot.application.turns import IncomingTurn, OutboundTarget

import luckbot.application.turn_runner as turn_runner_module


@dataclass
class _FakeRuntimeResult:
    final_text: str
    messages: list[object]


class _FakePluginManager:
    def __init__(
        self,
        *,
        plugins: list[tuple[str, str]] | None = None,
        services: dict[str, object] | None = None,
        tools: dict[str, object] | None = None,
        before_run_hooks: list[object] | None = None,
    ) -> None:
        self._plugins = list(plugins or [])
        self._services = dict(services or {})
        self._tools = dict(tools or {})
        self._before_run_hooks = list(before_run_hooks or [])
        self.initialized_with: tuple[list[object], list[str]] | None = None
        self.destroyed = False

    async def initialize(
        self,
        plugins: list[object] | None = None,
        plugin_dirs: list[str] | None = None,
    ) -> None:
        self.initialized_with = (list(plugins or []), list(plugin_dirs or []))

    async def destroy(self) -> None:
        self.destroyed = True

    def list_plugins(self) -> list[tuple[str, str]]:
        return list(self._plugins)

    def get_service(self, name: str) -> object | None:
        return self._services.get(name)

    def get_tools(self) -> dict[str, object]:
        return dict(self._tools)

    def get_hooks(self, hook_name: str) -> list[object]:
        if hook_name == "before_run":
            return list(self._before_run_hooks)
        return []


class _FakeSkillRegistry:
    def __init__(self, skills: list[object]) -> None:
        self._skills = {str(skill.name): skill for skill in skills}

    def all_skills(self) -> list[object]:
        return list(self._skills.values())

    def resolve(self, name: str) -> object | None:
        return self._skills.get(name)


def _incoming_turn(
    text: str,
    *,
    channel: str = "cli",
    transport: str = "local",
    session_key: str = "local:default",
    owner_id: str = "local",
) -> IncomingTurn:
    return IncomingTurn(
        channel=channel,
        transport=transport,
        chat_type="dm",
        chat_id="oc_x",
        user_id="ou_x",
        message_id="om_x",
        text=text,
        session_key=session_key,
        owner_id=owner_id,
        target=OutboundTarget(receive_id="ou_x", receive_id_type="open_id"),
    )


def test_tokenize_command_handles_slash_and_plain_text() -> None:
    assert tokenize_command("hello") is None

    parsed = tokenize_command("/session export --tail 10")
    assert parsed is not None
    assert parsed.name == "session"
    assert parsed.tokens == ["export", "--tail", "10"]


def test_render_help_text_lists_new_commands_only() -> None:
    text = render_help_text()

    assert "/session new" in text
    assert "/skill show <name>" in text
    assert "/new" not in text
    assert "/json" not in text


def test_local_and_gateway_cli_sessions_use_distinct_namespaces() -> None:
    assert build_local_session_key("default") == "local:default"
    assert build_gateway_cli_session_key("default") == "gateway:cli:default"


@pytest.mark.asyncio
async def test_execute_command_skill_requires_plugin_service() -> None:
    result = await execute_command(
        "/skill list",
        CommandContext(
            channel="cli",
            transport="cli",
            plugin_manager=_FakePluginManager(),
            conversation_history=[],
        ),
    )

    assert result.handled is True
    assert "未注册 skill_registry" in result.final_text


@pytest.mark.asyncio
async def test_execute_command_skill_uses_plugin_registry_service() -> None:
    registry = _FakeSkillRegistry(
        [
            SimpleNamespace(
                name="demo",
                description="demo skill",
                when_to_use="demo only",
                docs=[SimpleNamespace(name="guide.md", description="Guide")],
                location="/tmp/demo/SKILL.md",
            )
        ]
    )

    result = await execute_command(
        "/skill show demo",
        CommandContext(
            channel="cli",
            transport="cli",
            plugin_manager=_FakePluginManager(services={"skill_registry": registry}),
            conversation_history=[],
        ),
    )

    assert result.handled is True
    assert "Skill: demo" in result.final_text
    assert "guide.md: Guide" in result.final_text


@pytest.mark.asyncio
async def test_execute_command_mcp_list_uses_plugin_services() -> None:
    calls: list[str] = []

    def _config_snapshot() -> object:
        calls.append("config")
        return SimpleNamespace(
            path="/tmp/mcp.json",
            exists=True,
            error=None,
            servers={
                "demo": {
                    "transport": "stdio",
                    "command": "demo-server",
                }
            },
        )

    async def _tool_names() -> list[str]:
        calls.append("tools")
        return ["mcp_demo_echo"]

    result = await execute_command(
        "/mcp list",
        CommandContext(
            channel="cli",
            transport="cli",
            plugin_manager=_FakePluginManager(
                services={
                    "mcp_config_snapshot": _config_snapshot,
                    "mcp_tool_names": _tool_names,
                }
            ),
            conversation_history=[],
        ),
    )

    assert result.handled is True
    assert calls == ["config", "tools"]
    assert "demo: stdio demo-server" in result.final_text
    assert "mcp_demo_echo" in result.final_text


@pytest.mark.asyncio
async def test_execute_command_mcp_list_requires_plugin_services() -> None:
    result = await execute_command(
        "/mcp list",
        CommandContext(
            channel="cli",
            transport="cli",
            plugin_manager=_FakePluginManager(),
            conversation_history=[],
        ),
    )

    assert result.handled is True
    assert "未注册 MCP 查询服务" in result.final_text


@pytest.mark.asyncio
async def test_execute_command_session_new_uses_services_and_clears_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flush_calls: list[tuple[list[object], str | None, str | None]] = []
    sync_calls: list[str] = []
    begin_calls: list[tuple[str | None, str | None]] = []

    def _flush(messages: list[object], *, session_key: str | None, owner_id: str | None) -> str:
        flush_calls.append((messages, session_key, owner_id))
        return "已追加 2 条消息"

    async def _sync_now() -> None:
        sync_calls.append("sync")

    def _begin_new(*, session_key: str | None, owner_id: str | None) -> str:
        begin_calls.append((session_key, owner_id))
        return "已开始新会话: sess_new"

    async def _archive(_session_key: str, _paths: object, _history: list[object]) -> str:
        return "memory/demo.md"

    monkeypatch.setattr(
        "luckbot.application.commands.executor.resolve_memory_paths",
        lambda: object(),
    )
    monkeypatch.setattr(
        "luckbot.application.commands.executor.archive_last_session_to_markdown",
        _archive,
    )

    history = [HumanMessage(content="u"), AIMessage(content="a")]
    result = await execute_command(
        "/session new",
        CommandContext(
            channel="cli",
            transport="cli",
            plugin_manager=_FakePluginManager(
                services={
                    "session_flush": _flush,
                    "memory_sync_now": _sync_now,
                    "session_begin_new": _begin_new,
                }
            ),
            conversation_history=history,
            session_key="demo",
            owner_id="owner",
        ),
    )

    assert result.handled is True
    assert result.updated_conversation_history == []
    assert "memory/demo.md" in result.final_text
    assert flush_calls == [(history, "demo", "owner")]
    assert sync_calls == ["sync"]
    assert begin_calls == [("demo", "owner")]


@pytest.mark.asyncio
async def test_execute_command_session_export_rejects_invalid_tail() -> None:
    result = await execute_command(
        "/session export --tail nope",
        CommandContext(
            channel="cli",
            transport="cli",
            plugin_manager=_FakePluginManager(),
            conversation_history=[],
        ),
    )

    assert result.handled is True
    assert "需要正整数" in result.final_text


@pytest.mark.asyncio
async def test_agent_turn_runner_routes_command_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_SESSION_PERSIST", "0")
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(turn_runner_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(turn_runner_module, "discover_builtin_plugins", lambda: [])

    async def _raise_runtime(*_args, **_kwargs):
        raise AssertionError("run_runtime should not be called for slash commands")

    monkeypatch.setattr(turn_runner_module, "run_runtime", _raise_runtime)

    result = await AgentTurnRunner(max_steps=5).run_turn(_incoming_turn("/help"))

    assert result.handled_as_command is True
    assert "可用命令" in result.final_text
    assert result.messages == []
    assert fake_pm.destroyed is True


@pytest.mark.asyncio
async def test_agent_turn_runner_routes_plain_text_to_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_SESSION_PERSIST", "0")
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(turn_runner_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(turn_runner_module, "discover_builtin_plugins", lambda: [])

    async def _fake_runtime(spec):
        assert len(spec.messages) == 1
        assert isinstance(spec.messages[0], HumanMessage)
        assert spec.messages[0].content == "hello"
        assert spec.plugin_manager is fake_pm
        return _FakeRuntimeResult(
            final_text="done",
            messages=[HumanMessage(content="hello"), AIMessage(content="done")],
        )

    monkeypatch.setattr(turn_runner_module, "run_runtime", _fake_runtime)

    result = await AgentTurnRunner(max_steps=5).run_turn(_incoming_turn("hello"))

    assert result.handled_as_command is False
    assert result.final_text == "done"
    assert len(result.messages) == 2
    assert fake_pm.destroyed is True


@pytest.mark.asyncio
async def test_agent_turn_runner_handles_gateway_command_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    fake_pm = _FakePluginManager(plugins=[("demo-plugin", "1.0.0")])
    monkeypatch.setattr(turn_runner_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(turn_runner_module, "discover_builtin_plugins", lambda: [])

    async def _raise_runtime(*_args, **_kwargs):
        raise AssertionError("run_runtime should not be called for slash commands")

    monkeypatch.setattr(turn_runner_module, "run_runtime", _raise_runtime)

    incoming = _incoming_turn(
        "/plugin list",
        channel="feishu",
        transport="webhook",
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
    )

    result = await AgentTurnRunner().run_turn(incoming)

    assert "demo-plugin v1.0.0" in result.final_text
    assert fake_pm.initialized_with is not None
    assert fake_pm.initialized_with[1] == [str((tmp_path / ".luckbot" / "plugins").resolve())]
    assert fake_pm.destroyed is True


@pytest.mark.asyncio
async def test_agent_turn_runner_uses_runtime_for_gateway_plain_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(turn_runner_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(turn_runner_module, "discover_builtin_plugins", lambda: [])

    async def _fake_runtime(spec):
        assert len(spec.messages) == 1
        assert isinstance(spec.messages[0], HumanMessage)
        assert spec.messages[0].content == "hello"
        assert spec.plugin_manager is fake_pm
        assert spec.platform == "feishu"
        assert spec.session_key == "feishu:ou_x"
        assert spec.owner_id == "feishu:user:ou_x"
        return _FakeRuntimeResult(final_text="runtime done", messages=[HumanMessage(content="hello")])

    monkeypatch.setattr(turn_runner_module, "run_runtime", _fake_runtime)

    incoming = _incoming_turn(
        "hello",
        channel="feishu",
        transport="webhook",
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
    )

    result = await AgentTurnRunner().run_turn(incoming)

    assert result.final_text == "runtime done"
    assert fake_pm.initialized_with is not None
    assert fake_pm.initialized_with[1] == [str((tmp_path / ".luckbot" / "plugins").resolve())]
    assert fake_pm.destroyed is True


@pytest.mark.asyncio
async def test_agent_turn_runner_parses_inline_skill_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(turn_runner_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(turn_runner_module, "discover_builtin_plugins", lambda: [])

    async def _fake_runtime(spec):
        assert len(spec.messages) == 1
        assert isinstance(spec.messages[0], HumanMessage)
        assert spec.messages[0].content == "hello"
        assert spec.requested_skill is not None
        assert spec.requested_skill.skill_name == "demo"
        return _FakeRuntimeResult(final_text="runtime done", messages=[])

    monkeypatch.setattr(turn_runner_module, "run_runtime", _fake_runtime)

    incoming = _incoming_turn(
        "[use skill](demo) hello",
        channel="feishu",
        transport="webhook",
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
    )

    result = await AgentTurnRunner().run_turn(incoming)

    assert result.final_text == "runtime done"
    assert fake_pm.destroyed is True


@pytest.mark.asyncio
async def test_agent_turn_runner_uses_project_relative_plugin_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(turn_runner_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(turn_runner_module, "discover_builtin_plugins", lambda: [])

    async def _raise_runtime(*_args, **_kwargs):
        raise AssertionError("run_runtime should not be called for slash commands")

    monkeypatch.setattr(turn_runner_module, "run_runtime", _raise_runtime)

    await AgentTurnRunner().run_turn(_incoming_turn("/help"))

    assert fake_pm.initialized_with is not None
    assert fake_pm.initialized_with[1] == [str((tmp_path / ".luckbot" / "plugins").resolve())]
    assert fake_pm.destroyed is True


@pytest.mark.asyncio
async def test_agent_turn_runner_session_export_uses_persisted_history(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(turn_runner_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(turn_runner_module, "discover_builtin_plugins", lambda: [])

    async def _raise_runtime(*_args, **_kwargs):
        raise AssertionError("run_runtime should not be called for slash commands")

    monkeypatch.setattr(turn_runner_module, "run_runtime", _raise_runtime)

    session_id = resolve_session("feishu:ou_x").session_id
    lines = messages_to_jsonl_lines(
        [HumanMessage(content="u1"), AIMessage(content="a1")],
        run_id="rid",
        start_index=0,
    )
    append_transcript_lines(session_id, lines)

    incoming = _incoming_turn(
        "/session export --tail 1",
        channel="feishu",
        transport="webhook",
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
    )

    result = await AgentTurnRunner().run_turn(incoming)

    assert '"role": "assistant"' in result.final_text
    assert '"content": "a1"' in result.final_text
