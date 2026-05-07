from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from luckbot.core.observability import ensure_observability_context, use_observability_context
from luckbot.core.runtime import RuntimeContext
from luckbot.domains.memory.compaction import _keep_recent_messages
from luckbot.domains.session.message_types import (
    build_compaction_summary_message,
    is_compaction_summary_message,
)
from luckbot.domains.session.state import transcript_path
from luckbot.domains.session.transcript import load_transcript_messages, rewrite_transcript_messages


def _tool_call(name: str, tool_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": {},
                "id": tool_id,
                "type": "tool_call",
            }
        ],
    )


def test_compaction_summary_round_trips_through_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    session_id = "trace-audit-roundtrip"
    original = [
        HumanMessage(content="你好"),
        build_compaction_summary_message("已确认方案 A，待补测试。"),
        AIMessage(content="收到"),
    ]

    rewrite_transcript_messages(session_id, original)
    loaded = load_transcript_messages(session_id)

    assert len(loaded) == 3
    assert isinstance(loaded[0], HumanMessage)
    assert is_compaction_summary_message(loaded[1])
    assert loaded[1].content == original[1].content
    assert isinstance(loaded[2], AIMessage)


def test_regular_system_message_is_not_persisted_to_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    session_id = "trace-audit-skip-system"

    rewrite_transcript_messages(
        session_id,
        [
            HumanMessage(content="u"),
            SystemMessage(content="base prompt"),
            AIMessage(content="a"),
        ],
    )
    loaded = load_transcript_messages(session_id)
    raw = transcript_path(session_id).read_text(encoding="utf-8")

    assert len(loaded) == 2
    assert all(not isinstance(msg, SystemMessage) for msg in loaded)
    assert '"role": "system"' not in raw
    assert "base prompt" not in raw


def test_transcript_load_drops_orphan_tool_message(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    session_id = "orphan-tool-load"
    path = transcript_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"timestamp": 1, "run_id": "r", "message": {"role": "system", "kind": "luckbot_compaction_summary", "content": "[此前对话已压缩摘要]\\n摘要"}}',
                '{"timestamp": 2, "run_id": "r", "message": {"role": "tool", "content": "bad", "tool_call_id": "call-1"}}',
                '{"timestamp": 3, "run_id": "r", "message": {"role": "user", "content": "hi"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_transcript_messages(session_id)

    assert len(loaded) == 2
    assert is_compaction_summary_message(loaded[0])
    assert isinstance(loaded[1], HumanMessage)
    assert not any(isinstance(msg, ToolMessage) for msg in loaded)


def test_transcript_keeps_complete_tool_call_group(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    session_id = "complete-tool-group"

    rewrite_transcript_messages(
        session_id,
        [
            HumanMessage(content="run"),
            _tool_call("demo", "call-1"),
            ToolMessage(content="ok", tool_call_id="call-1"),
            AIMessage(content="done"),
        ],
    )
    loaded = load_transcript_messages(session_id)

    assert len(loaded) == 4
    assert isinstance(loaded[1], AIMessage)
    assert isinstance(loaded[2], ToolMessage)
    assert loaded[2].tool_call_id == "call-1"


def test_transcript_drops_incomplete_tool_call_group(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    session_id = "incomplete-tool-group"

    rewrite_transcript_messages(
        session_id,
        [
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "one", "args": {}, "id": "call-1", "type": "tool_call"},
                    {"name": "two", "args": {}, "id": "call-2", "type": "tool_call"},
                ],
            ),
            ToolMessage(content="one", tool_call_id="call-1"),
            AIMessage(content="after"),
        ],
    )
    loaded = load_transcript_messages(session_id)

    assert len(loaded) == 2
    assert isinstance(loaded[0], HumanMessage)
    assert isinstance(loaded[1], AIMessage)
    assert loaded[1].content == "after"


def test_compaction_keep_recent_preserves_tool_call_group() -> None:
    messages = [
        HumanMessage(content="old"),
        _tool_call("demo", "call-1"),
        ToolMessage(content="ok", tool_call_id="call-1"),
        AIMessage(content="done"),
        HumanMessage(content="new"),
    ]

    kept = _keep_recent_messages(messages, keep=3)

    assert len(kept) == 4
    assert isinstance(kept[0], AIMessage)
    assert isinstance(kept[1], ToolMessage)
    assert isinstance(kept[2], AIMessage)
    assert isinstance(kept[3], HumanMessage)


def test_compaction_keep_recent_drops_bad_tool_history() -> None:
    messages = [
        build_compaction_summary_message("摘要"),
        ToolMessage(content="bad", tool_call_id="call-1"),
        HumanMessage(content="new"),
    ]

    kept = _keep_recent_messages(messages, keep=10)

    assert len(kept) == 2
    assert is_compaction_summary_message(kept[0])
    assert isinstance(kept[1], HumanMessage)


def test_observability_context_keeps_trace_and_run_identity() -> None:
    ctx = ensure_observability_context(
        trace_id="gw-event-1",
        run_id="run-1",
        platform="feishu",
    )

    with use_observability_context(ctx):
        child = ensure_observability_context()

    assert child.trace_id == "gw-event-1"
    assert child.run_id == "run-1"
    assert child.platform == "feishu"
    assert not hasattr(child, "session_key")
    assert not hasattr(child, "owner_id")


def test_runtime_context_merges_identity_into_run_attributes() -> None:
    runtime_ctx = RuntimeContext(
        system_prompt="base",
        messages=[HumanMessage(content="hello")],
        max_steps=1,
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
        trace_id="gw-event-1",
    )
    obs_ctx = runtime_ctx.to_observability_context()
    runtime_ctx.sync_observability_context(obs_ctx)

    attrs = runtime_ctx.as_run_attrs(obs_ctx)

    assert attrs["luckbot.trace_id"] == "gw-event-1"
    assert attrs["luckbot.session_key"] == "feishu:ou_x"
    assert attrs["luckbot.owner_id"] == "feishu:user:ou_x"
    assert "session_key" not in obs_ctx.as_metadata()
    assert "owner_id" not in obs_ctx.as_metadata()
