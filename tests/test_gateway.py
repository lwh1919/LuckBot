from __future__ import annotations

import asyncio
import importlib
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from luckbot.plugins.builtin.session_plugin import SessionPlugin
from luckbot.core.plugin.base import PluginContext
from luckbot.core.plugin.hooks import AfterRunInput, BeforeRunInput
from luckbot.adapters.gateway.dispatcher import SessionBusyError
from luckbot.adapters.gateway.feishu.adapter import FeishuAdapter
from luckbot.adapters.gateway.dispatcher import GatewayDispatcher
from luckbot.adapters.gateway.types import GatewayRunResult, IncomingEnvelope, OutboundTarget

gateway_app_module = importlib.import_module("luckbot.adapters.gateway.app")
dispatcher_module = importlib.import_module("luckbot.adapters.gateway.dispatcher")


class _FakeResponder:
    def __init__(self) -> None:
        self.progress: list[str] = []
        self.final: list[str] = []
        self.errors: list[str] = []

    async def send_progress(self, text: str) -> None:
        self.progress.append(text)

    async def send_final(self, text: str) -> None:
        self.final.append(text)

    async def send_error(self, text: str) -> None:
        self.errors.append(text)


class _FakeAdapter:
    name = "fake"

    def __init__(self) -> None:
        self.responders: list[_FakeResponder] = []

    def verify_request(self, headers: dict[str, str], body: bytes) -> bool:
        del headers, body
        return True

    def parse_request(self, headers: dict[str, str], body: bytes):
        del headers, body
        raise AssertionError("not used")

    async def create_responder(self, incoming: IncomingEnvelope) -> _FakeResponder:
        del incoming
        responder = _FakeResponder()
        self.responders.append(responder)
        return responder


class _FakeRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []

    async def run_turn(self, incoming: IncomingEnvelope) -> GatewayRunResult:
        self.calls.append(incoming.session_key)
        self.started.set()
        await self.release.wait()
        return GatewayRunResult(final_text="done", messages=[])


class _ImmediateRunner:
    async def run_turn(self, incoming: IncomingEnvelope) -> GatewayRunResult:
        del incoming
        return GatewayRunResult(final_text="done", messages=[])


class _FailingRunner:
    async def run_turn(self, incoming: IncomingEnvelope) -> GatewayRunResult:
        del incoming
        raise RuntimeError("boom")


class _FakeFeishuClient:
    def __init__(self, *, fail_update: bool = False, fail_send_card: bool = False) -> None:
        self.fail_update = fail_update
        self.fail_send_card = fail_send_card
        self.sent_cards: list[tuple[str, str, dict[str, object]]] = []
        self.updated_cards: list[tuple[str, dict[str, object]]] = []
        self.sent_texts: list[tuple[str, str, str]] = []

    async def send_card(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        card: dict[str, object],
    ) -> str:
        if self.fail_send_card:
            raise RuntimeError("send-card boom")
        self.sent_cards.append((receive_id, receive_id_type, card))
        return "msg_card_1"

    async def update_card(self, *, message_id: str, card: dict[str, object]) -> None:
        if self.fail_update:
            raise RuntimeError("update-card boom")
        self.updated_cards.append((message_id, card))

    async def send_text(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        text: str,
    ) -> str:
        self.sent_texts.append((receive_id, receive_id_type, text))
        return "msg_text_1"


def _incoming(session_key: str = "feishu:u1") -> IncomingEnvelope:
    return IncomingEnvelope(
        platform="feishu",
        chat_type="dm",
        chat_id="oc_x",
        user_id="ou_x",
        message_id="om_x",
        text="hello",
        session_key=session_key,
        owner_id="feishu:user:ou_x",
        target=OutboundTarget(receive_id="ou_x", receive_id_type="open_id"),
    )


def test_feishu_adapter_parses_private_text_message() -> None:
    adapter = FeishuAdapter(verification_token="verify-token", app_id="cli_x")
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt_123",
            "token": "verify-token",
            "app_id": "cli_x",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_x"}},
            "message": {
                "message_id": "om_x",
                "message_type": "text",
                "chat_id": "oc_x",
                "chat_type": "p2p",
                "content": json.dumps({"text": "你好"}),
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")

    assert adapter.verify_request({}, body) is True
    parsed = adapter.parse_request({}, body)

    assert parsed.incoming is not None
    assert parsed.incoming.session_key == "feishu:ou_x"
    assert parsed.incoming.owner_id == "feishu:user:ou_x"
    assert parsed.incoming.trace_id == "evt_123"
    assert parsed.incoming.target.receive_id_type == "open_id"
    assert parsed.incoming.text == "你好"


def test_feishu_adapter_accepts_top_level_url_verification_token() -> None:
    adapter = FeishuAdapter(verification_token="verify-token", app_id="cli_x")
    payload = {
        "type": "url_verification",
        "token": "verify-token",
        "app_id": "cli_x",
        "challenge": "challenge_x",
    }
    body = json.dumps(payload).encode("utf-8")

    assert adapter.verify_request({}, body) is True


def test_feishu_adapter_requires_mentions_in_group() -> None:
    adapter = FeishuAdapter()
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_x"}},
            "message": {
                "message_id": "om_group",
                "message_type": "text",
                "chat_id": "oc_group",
                "chat_type": "group",
                "content": json.dumps({"text": "@LuckBot 帮我看一下"}),
                "mentions": [{"id": {"open_id": "ou_bot"}}],
            },
        }
    }
    parsed = adapter.parse_request({}, json.dumps(payload).encode("utf-8"))

    assert parsed.incoming is not None
    assert parsed.incoming.session_key == "feishu:group:oc_group:ou_x"
    assert parsed.incoming.target.receive_id_type == "chat_id"
    assert "LuckBot" not in parsed.incoming.text


def test_feishu_adapter_accepts_group_mentions_without_open_id_shape() -> None:
    adapter = FeishuAdapter()
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_x"}},
            "message": {
                "message_id": "om_group_shape",
                "message_type": "text",
                "chat_id": "oc_group",
                "chat_type": "group",
                "content": json.dumps({"text": "@LuckBot /help"}),
                "mentions": [{"key": "@_user_1", "name": "LuckBot"}],
            },
        }
    }

    parsed = adapter.parse_request({}, json.dumps(payload).encode("utf-8"))

    assert parsed.incoming is not None
    assert parsed.incoming.session_key == "feishu:group:oc_group:ou_x"
    assert parsed.incoming.text == "/help"


@pytest.mark.asyncio
async def test_feishu_responder_falls_back_when_card_update_fails() -> None:
    adapter = FeishuAdapter(client=_FakeFeishuClient(fail_update=True))
    responder = await adapter.create_responder(_incoming())

    await responder.send_progress("working")
    await responder.send_final("done")

    client = adapter._client
    assert isinstance(client, _FakeFeishuClient)
    assert len(client.sent_cards) == 2
    assert client.sent_texts == []


@pytest.mark.asyncio
async def test_feishu_responder_falls_back_to_text_when_card_send_fails() -> None:
    adapter = FeishuAdapter(client=_FakeFeishuClient(fail_send_card=True))
    responder = await adapter.create_responder(_incoming())

    await responder.send_final("done")

    client = adapter._client
    assert isinstance(client, _FakeFeishuClient)
    assert client.sent_texts == [("ou_x", "open_id", "done")]


def test_gateway_app_relies_on_framework_http_instrumentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_app_module, "init_observability", lambda **_kwargs: None)

    app = gateway_app_module.create_app()

    assert app.user_middleware == []

    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_gateway_app_handles_feishu_url_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_app_module, "init_observability", lambda **_kwargs: None)

    app = gateway_app_module.create_app()
    client = TestClient(app)
    response = client.post(
        "/webhooks/feishu/events",
        json={
            "type": "url_verification",
            "challenge": "test_challenge_123",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "test_challenge_123"}


def test_gateway_app_handles_cli_turn_and_namespaces_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_app_module, "init_observability", lambda **_kwargs: None)

    captured: list[IncomingEnvelope] = []

    class _FakeDispatcher:
        def __init__(self, _adapter, _runner) -> None:
            pass

        async def enqueue(self, _incoming: IncomingEnvelope) -> bool:
            raise AssertionError("not used")

        async def run_inline(self, incoming: IncomingEnvelope) -> GatewayRunResult:
            captured.append(incoming)
            return GatewayRunResult(final_text="cli done", messages=[])

    monkeypatch.setattr(gateway_app_module, "GatewayDispatcher", _FakeDispatcher)

    app = gateway_app_module.create_app()
    client = TestClient(app)
    response = client.post(
        "/gateway/cli/turn",
        json={"text": "hello", "session_key": "demo"},
    )

    assert response.status_code == 200
    assert response.json() == {"final_text": "cli done"}
    assert len(captured) == 1
    assert captured[0].platform == "cli"
    assert captured[0].session_key == "gateway:cli:demo"
    assert captured[0].owner_id == "local"


def test_gateway_app_preserves_explicit_cli_owner_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_app_module, "init_observability", lambda **_kwargs: None)

    captured: list[IncomingEnvelope] = []

    class _FakeDispatcher:
        def __init__(self, _adapter, _runner) -> None:
            pass

        async def enqueue(self, _incoming: IncomingEnvelope) -> bool:
            raise AssertionError("not used")

        async def run_inline(self, incoming: IncomingEnvelope) -> GatewayRunResult:
            captured.append(incoming)
            return GatewayRunResult(final_text="cli done", messages=[])

    monkeypatch.setattr(gateway_app_module, "GatewayDispatcher", _FakeDispatcher)

    app = gateway_app_module.create_app()
    client = TestClient(app)
    response = client.post(
        "/gateway/cli/turn",
        json={"text": "hello", "session_key": "demo", "owner_id": "alice"},
    )

    assert response.status_code == 200
    assert response.json() == {"final_text": "cli done"}
    assert len(captured) == 1
    assert captured[0].owner_id == "alice"


def test_gateway_app_cli_turn_returns_409_when_session_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_app_module, "init_observability", lambda **_kwargs: None)

    class _BusyDispatcher:
        def __init__(self, _adapter, _runner) -> None:
            pass

        async def enqueue(self, _incoming: IncomingEnvelope) -> bool:
            raise AssertionError("not used")

        async def run_inline(self, incoming: IncomingEnvelope) -> GatewayRunResult:
            del incoming
            raise SessionBusyError("上一条消息仍在处理中，请稍后再试。")

    monkeypatch.setattr(gateway_app_module, "GatewayDispatcher", _BusyDispatcher)

    app = gateway_app_module.create_app()
    client = TestClient(app)
    response = client.post(
        "/gateway/cli/turn",
        json={"text": "hello", "session_key": "demo"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "busy"


@pytest.mark.asyncio
async def test_gateway_dispatcher_rejects_concurrent_run_for_same_session() -> None:
    adapter = _FakeAdapter()
    runner = _FakeRunner()
    dispatcher = GatewayDispatcher(adapter, runner)

    accepted = await dispatcher.enqueue(_incoming("feishu:u1"))
    assert accepted is True
    await runner.started.wait()

    second = await dispatcher.enqueue(_incoming("feishu:u1"))
    assert second is False
    assert len(adapter.responders) == 2
    assert adapter.responders[1].errors == ["上一条消息仍在处理中，请稍后再试。"]

    runner.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert adapter.responders[0].progress == ["LuckBot 正在处理中..."]
    assert adapter.responders[0].final == ["done"]


@pytest.mark.asyncio
async def test_gateway_dispatcher_records_success_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeAdapter()
    dispatcher = GatewayDispatcher(adapter, _ImmediateRunner())
    counters: list[tuple[str, int, dict[str, object] | None]] = []
    histograms: list[tuple[str, int | float, dict[str, object] | None]] = []

    monkeypatch.setattr(
        dispatcher_module,
        "increment_counter",
        lambda name, value=1, *, attributes=None: counters.append((name, value, attributes)),
    )
    monkeypatch.setattr(
        dispatcher_module,
        "record_histogram",
        lambda name, value, *, attributes=None: histograms.append((name, value, attributes)),
    )

    accepted = await dispatcher.enqueue(_incoming("feishu:u-success"))
    assert accepted is True

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(adapter.responders) == 1
    assert adapter.responders[0].progress == ["LuckBot 正在处理中..."]
    assert adapter.responders[0].final == ["done"]
    assert any(name == "luckbot_gateway_enqueued_total" for name, _value, _attrs in counters)
    assert any(name == "luckbot_gateway_runs_total" for name, _value, _attrs in counters)
    assert any(
        name == "luckbot_gateway_run_duration"
        and attrs is not None
        and attrs.get("outcome") == "success"
        for name, _value, attrs in histograms
    )


@pytest.mark.asyncio
async def test_gateway_dispatcher_records_error_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeAdapter()
    dispatcher = GatewayDispatcher(adapter, _FailingRunner())
    counters: list[tuple[str, int, dict[str, object] | None]] = []
    histograms: list[tuple[str, int | float, dict[str, object] | None]] = []

    monkeypatch.setattr(
        dispatcher_module,
        "increment_counter",
        lambda name, value=1, *, attributes=None: counters.append((name, value, attributes)),
    )
    monkeypatch.setattr(
        dispatcher_module,
        "record_histogram",
        lambda name, value, *, attributes=None: histograms.append((name, value, attributes)),
    )

    accepted = await dispatcher.enqueue(_incoming("feishu:u-error"))
    assert accepted is True

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(adapter.responders) == 1
    assert adapter.responders[0].progress == ["LuckBot 正在处理中..."]
    assert adapter.responders[0].errors == ["boom"]
    assert any(name == "luckbot_gateway_enqueued_total" for name, _value, _attrs in counters)
    assert any(name == "luckbot_gateway_errors_total" for name, _value, _attrs in counters)
    assert not any(name == "luckbot_gateway_runs_total" for name, _value, _attrs in counters)
    assert any(
        name == "luckbot_gateway_run_duration"
        and attrs is not None
        and attrs.get("outcome") == "error"
        for name, _value, attrs in histograms
    )


@pytest.mark.asyncio
async def test_session_plugin_uses_runtime_session_and_owner_identity(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_SESSION", "default")
    monkeypatch.setenv("LUCKBOT_OWNER_ID", "local")

    plugin = SessionPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)

    before = await plugin._before_run(
        BeforeRunInput(
            tools={},
            system_prompt="base",
            conversation_history=[],
            session_key="feishu:ou_x",
            owner_id="feishu:user:ou_x",
        )
    )
    assert before is None or before.conversation_history in (None, [])

    await plugin._after_run(
        AfterRunInput(
            result="ok",
            messages=[HumanMessage(content="u"), AIMessage(content="a")],
            session_key="feishu:ou_x",
            owner_id="feishu:user:ou_x",
        )
    )

    index_path = tmp_path / "sessions" / "sessions.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["feishu:ou_x"]["owner_id"] == "feishu:user:ou_x"
    session_id = data["feishu:ou_x"]["session_id"]
    transcript_path = tmp_path / "sessions" / f"{session_id}.jsonl"
    assert transcript_path.is_file()
