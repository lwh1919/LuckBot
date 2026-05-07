from __future__ import annotations

import json
from pathlib import Path

import pytest

from luckbot.domains.memory.memory_tools import build_memory_get_tool
from luckbot.domains.memory.paths import ensure_memory_tree, resolve_memory_paths


@pytest.mark.asyncio
async def test_memory_get_rejects_session_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    paths = resolve_memory_paths()
    ensure_memory_tree(paths)
    tool = build_memory_get_tool(paths)

    result = json.loads(await tool.ainvoke({"path": "session:sess_abc"}))

    assert result == {"error": "path 不允许", "path": "session:sess_abc"}


@pytest.mark.asyncio
async def test_memory_get_reads_registered_extra_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    extra = tmp_path / "extra.md"
    extra.write_text("extra memory", encoding="utf-8")
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LUCKBOT_MEMORY_EXTRA_PATHS", str(extra))
    paths = resolve_memory_paths()
    tool = build_memory_get_tool(paths)

    result = json.loads(await tool.ainvoke({"path": "extra/extra.md"}))

    assert result == {"text": "extra memory", "path": "extra/extra.md"}


def test_resolve_memory_paths_uses_owner_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LUCKBOT_OWNER_ID", "user:lwh")

    paths = resolve_memory_paths()

    assert paths.owner_id == "user:lwh"
    assert Path(paths.memory_root).name == "user:lwh"
