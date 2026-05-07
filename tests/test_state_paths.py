from __future__ import annotations

from pathlib import Path

from luckbot.domains.session.state import resolve_state_dir


def test_resolve_state_dir_defaults_to_project_local_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("LUCKBOT_STATE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    resolved = resolve_state_dir()

    assert resolved == (tmp_path / ".luckbot" / "state").resolve()


def test_resolve_state_dir_does_not_seed_from_legacy_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    home_root = tmp_path / "home"
    legacy_root = home_root / ".luckbot"
    legacy_sessions = legacy_root / "sessions"
    legacy_memory = legacy_root / "memory" / "local"
    legacy_sessions.mkdir(parents=True, exist_ok=True)
    legacy_memory.mkdir(parents=True, exist_ok=True)
    (legacy_sessions / "sessions.json").write_text('{"default":{"session_id":"sess_1"}}\n', encoding="utf-8")
    (legacy_memory / "MEMORY.md").write_text("# legacy memory\n", encoding="utf-8")

    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("LUCKBOT_STATE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(home_root))

    resolved = resolve_state_dir()

    assert resolved == (project_root / ".luckbot" / "state").resolve()
    assert not (resolved / "sessions" / "sessions.json").exists()
    assert not (resolved / "memory" / "local" / "MEMORY.md").exists()
