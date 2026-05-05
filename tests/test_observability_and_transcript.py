from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from luckbot.core.observability import ensure_observability_context, use_observability_context
from luckbot.domains.session.message_types import (
    build_compaction_summary_message,
    is_compaction_summary_message,
)
from luckbot.domains.session.state import transcript_path
from luckbot.domains.session.transcript import load_transcript_messages, rewrite_transcript_messages


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


def test_observability_context_keeps_trace_and_run_identity() -> None:
    ctx = ensure_observability_context(
        trace_id="gw-event-1",
        run_id="run-1",
        session_key="feishu:ou_x",
        owner_id="feishu:user:ou_x",
        platform="feishu",
    )

    with use_observability_context(ctx):
        child = ensure_observability_context(owner_id="feishu:user:ou_x")

    assert child.trace_id == "gw-event-1"
    assert child.run_id == "run-1"
    assert child.platform == "feishu"
