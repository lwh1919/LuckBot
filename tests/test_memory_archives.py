from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import luckbot.domains.memory.session_memory as session_memory_module
from luckbot.domains.memory.paths import ensure_memory_tree, resolve_memory_paths
from luckbot.domains.memory.session_memory import (
    SessionArchivePayload,
    archive_last_session_to_markdown,
)


@pytest.mark.asyncio
async def test_session_archive_uses_unique_session_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))

    async def _payload(**_kwargs) -> SessionArchivePayload:
        return SessionArchivePayload(
            slug="same-topic",
            summary="summary",
            key_points=["point"],
            conversation_highlights=["highlight"],
            action_items=[],
        )

    monkeypatch.setattr(session_memory_module, "_build_archive_payload", _payload)
    paths = resolve_memory_paths()
    ensure_memory_tree(paths)
    messages = [HumanMessage(content="u"), AIMessage(content="a")]

    first = await archive_last_session_to_markdown(
        "gateway:cli:default",
        paths,
        messages,
        session_id="session-one-abcdef",
    )
    second = await archive_last_session_to_markdown(
        "feishu:ou_x",
        paths,
        messages,
        session_id="session-two-abcdef",
    )

    assert first is not None
    assert second is not None
    assert first != second
    assert first.startswith("memory/sessions/")
    assert second.startswith("memory/sessions/")
    assert (Path(paths.memory_root) / first).is_file()
    assert (Path(paths.memory_root) / second).is_file()


@pytest.mark.asyncio
async def test_session_archive_does_not_overwrite_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))

    async def _payload(**_kwargs) -> SessionArchivePayload:
        return SessionArchivePayload(
            slug="same-topic",
            summary="summary",
            key_points=["point"],
            conversation_highlights=["highlight"],
            action_items=[],
        )

    monkeypatch.setattr(session_memory_module, "_build_archive_payload", _payload)
    paths = resolve_memory_paths()
    ensure_memory_tree(paths)
    messages = [HumanMessage(content="u"), AIMessage(content="a")]

    first = await archive_last_session_to_markdown(
        "gateway:cli:default",
        paths,
        messages,
        session_id="session-one-abcdef",
    )
    assert first is not None
    first_path = Path(paths.memory_root) / first
    first_path.write_text("keep me", encoding="utf-8")

    second = await archive_last_session_to_markdown(
        "gateway:cli:default",
        paths,
        messages,
        session_id="session-one-abcdef",
    )

    assert second is not None
    assert second != first
    assert first_path.read_text(encoding="utf-8") == "keep me"
    assert (Path(paths.memory_root) / second).is_file()
