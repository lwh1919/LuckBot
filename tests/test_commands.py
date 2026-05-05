from __future__ import annotations

import importlib
from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from luckbot.domains.session.state import resolve_session
from luckbot.domains.session import build_gateway_cli_session_key, build_local_session_key
from luckbot.domains.session.transcript import append_transcript_lines, messages_to_jsonl_lines
from luckbot.application.commands import CommandContext, execute_command, render_help_text, tokenize_command
from luckbot.adapters.gateway.types import IncomingEnvelope, OutboundTarget

cli_module = importlib.import_module("luckbot.entrypoints.cli")
gateway_service_module = importlib.import_module("luckbot.adapters.gateway.service")


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
async def test_execute_command_skill_show_reports_missing_skill() -> None:
    result = await execute_command(
        "/skill show missing",
        CommandContext(
            transport="cli",
            plugin_manager=_FakePluginManager(),
            conversation_history=[],
        ),
    )

    assert result.handled is True
    assert "未找到名为" in result.final_text


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
            transport="cli",
            plugin_manager=_FakePluginManager(),
            conversation_history=[],
        ),
    )

    assert result.handled is True
    assert "需要正整数" in result.final_text


@pytest.mark.asyncio
async def test_cli_handle_user_input_routes_command_without_agent_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise_agent_loop(*_args, **_kwargs):
        raise AssertionError("agent_loop should not be called for slash commands")

    monkeypatch.setattr(cli_module, "agent_loop", _raise_agent_loop)

    result, updated_history, handled = await cli_module._handle_user_input(
        "/help",
        pm=_FakePluginManager(),
        conversation_history=[],
        session_key="default",
        max_steps=5,
    )

    assert handled is True
    assert "可用命令" in result
    assert updated_history == []


@pytest.mark.asyncio
async def test_cli_handle_user_input_routes_plain_text_to_agent_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_agent_loop(*_args, **_kwargs):
        return ("done", [HumanMessage(content="hello"), AIMessage(content="done")])

    monkeypatch.setattr(cli_module, "agent_loop", _fake_agent_loop)

    result, updated_history, handled = await cli_module._handle_user_input(
        "hello",
        pm=_FakePluginManager(),
        conversation_history=[],
        session_key="default",
        max_steps=5,
    )

    assert handled is False
    assert result == "done"
    assert len(updated_history) == 2


@pytest.mark.asyncio
async def test_run_once_gateway_routes_plain_text_to_gateway_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setenv("LUCKBOT_SESSION", "gateway-demo")
    monkeypatch.setattr(cli_module, "_ensure_gateway_running", lambda: None)

    def _fake_send_turn(text: str, *, session_name: str | None = None, owner_id=None, trace_id=None):
        del owner_id, trace_id
        calls.append(f"{session_name}:{text}")
        return type("_R", (), {"final_text": "done"})()

    monkeypatch.setattr(cli_module, "send_gateway_turn", _fake_send_turn)

    rc = await cli_module._run_once_gateway("hello", as_command=False)

    assert rc == 0
    assert calls == ["gateway-demo:hello"]


@pytest.mark.asyncio
async def test_run_once_gateway_routes_slash_command_to_gateway_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setenv("LUCKBOT_SESSION", "gateway-demo")
    monkeypatch.setattr(cli_module, "_ensure_gateway_running", lambda: None)

    def _fake_send_command(text: str, *, session_name: str | None = None, owner_id=None, trace_id=None):
        del owner_id, trace_id
        calls.append(f"{session_name}:{text}")
        return type("_R", (), {"final_text": "done"})()

    monkeypatch.setattr(cli_module, "send_gateway_command", _fake_send_command)

    rc = await cli_module._run_once_gateway("/help", as_command=True)

    assert rc == 0
    assert calls == ["gateway-demo:/help"]


@pytest.mark.asyncio
async def test_gateway_service_handles_command_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    fake_pm = _FakePluginManager(plugins=[("demo-plugin", "1.0.0")])
    monkeypatch.setattr(gateway_service_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(gateway_service_module, "discover_builtin_plugins", lambda: [])

    async def _raise_runtime(*_args, **_kwargs):
        raise AssertionError("run_runtime should not be called for slash commands")

    monkeypatch.setattr(gateway_service_module, "run_runtime", _raise_runtime)

    service = gateway_service_module.LuckBotGatewayService()
    incoming = IncomingEnvelope(
        platform="feishu",
        chat_type="dm",
        chat_id="oc_x",
        user_id="ou_x",
        message_id="om_x",
        text="/plugin list",
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
        target=OutboundTarget(receive_id="ou_x", receive_id_type="open_id"),
    )

    result = await service.run_turn(incoming)

    assert "demo-plugin v1.0.0" in result.final_text
    assert fake_pm.initialized_with is not None
    assert fake_pm.initialized_with[1] == [str((tmp_path / ".luckbot" / "plugins").resolve())]
    assert fake_pm.destroyed is True


@pytest.mark.asyncio
async def test_gateway_service_uses_runtime_for_plain_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(gateway_service_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(gateway_service_module, "discover_builtin_plugins", lambda: [])

    async def _fake_runtime(spec):
        assert spec.user_input == "hello"
        assert spec.plugin_manager is fake_pm
        return _FakeRuntimeResult(final_text="runtime done", messages=[HumanMessage(content="hello")])

    monkeypatch.setattr(gateway_service_module, "run_runtime", _fake_runtime)

    service = gateway_service_module.LuckBotGatewayService()
    incoming = IncomingEnvelope(
        platform="feishu",
        chat_type="dm",
        chat_id="oc_x",
        user_id="ou_x",
        message_id="om_x",
        text="hello",
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
        target=OutboundTarget(receive_id="ou_x", receive_id_type="open_id"),
    )

    result = await service.run_turn(incoming)

    assert result.final_text == "runtime done"
    assert fake_pm.initialized_with is not None
    assert fake_pm.initialized_with[1] == [str((tmp_path / ".luckbot" / "plugins").resolve())]
    assert fake_pm.destroyed is True


@pytest.mark.asyncio
async def test_build_plugin_manager_uses_project_relative_plugin_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(cli_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(cli_module, "discover_builtin_plugins", lambda: [])

    pm = await cli_module._build_plugin_manager()

    assert pm is fake_pm
    assert fake_pm.initialized_with is not None
    assert fake_pm.initialized_with[1] == [str((tmp_path / ".luckbot" / "plugins").resolve())]


@pytest.mark.asyncio
async def test_gateway_session_export_uses_persisted_history(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    fake_pm = _FakePluginManager()
    monkeypatch.setattr(gateway_service_module, "PluginManager", lambda: fake_pm)
    monkeypatch.setattr(gateway_service_module, "discover_builtin_plugins", lambda: [])

    async def _raise_runtime(*_args, **_kwargs):
        raise AssertionError("run_runtime should not be called for slash commands")

    monkeypatch.setattr(gateway_service_module, "run_runtime", _raise_runtime)

    session_id = resolve_session("feishu:ou_x").session_id
    lines = messages_to_jsonl_lines(
        [HumanMessage(content="u1"), AIMessage(content="a1")],
        run_id="rid",
        start_index=0,
    )
    append_transcript_lines(session_id, lines)

    service = gateway_service_module.LuckBotGatewayService()
    incoming = IncomingEnvelope(
        platform="feishu",
        chat_type="dm",
        chat_id="oc_x",
        user_id="ou_x",
        message_id="om_x",
        text="/session export --tail 1",
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
        target=OutboundTarget(receive_id="ou_x", receive_id_type="open_id"),
    )

    result = await service.run_turn(incoming)

    assert '"role": "assistant"' in result.final_text
    assert '"content": "a1"' in result.final_text
