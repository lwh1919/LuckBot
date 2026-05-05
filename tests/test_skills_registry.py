from __future__ import annotations

from pathlib import Path

from luckbot.domains.skills.registry import SkillRegistry


def _write_skill_with_docs(root: Path, name: str) -> None:
    skill_dir = root / name
    (skill_dir / "docs").mkdir(parents=True, exist_ok=True)
    (skill_dir / "refs").mkdir(parents=True, exist_ok=True)
    (skill_dir / "extra").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            "description: registry test skill\n"
            "---\n\n"
            "# body\n"
        ),
        encoding="utf-8",
    )
    (skill_dir / "docs" / "guide.md").write_text(
        "# docs guide\n\nfrom docs\n",
        encoding="utf-8",
    )
    (skill_dir / "refs" / "guide.md").write_text(
        "# refs guide\n\nfrom refs\n",
        encoding="utf-8",
    )
    (skill_dir / "extra" / "usage.md").write_text(
        "# usage\n\nfrom usage\n",
        encoding="utf-8",
    )


def test_skill_registry_uses_relative_doc_paths_and_unique_basename_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skills_root = tmp_path / "skills"
    _write_skill_with_docs(skills_root, "demo")
    monkeypatch.setenv("LUCKBOT_SKILLS_DIR", str(skills_root))

    reg = SkillRegistry()
    assert reg.refresh_if_changed() is True

    assert reg.doc_names("demo") == [
        "docs/guide.md",
        "extra/usage.md",
        "refs/guide.md",
    ]
    assert reg.load_doc("demo", "docs/guide.md") == "# docs guide\n\nfrom docs\n"
    assert reg.load_doc("demo", "usage.md") == "# usage\n\nfrom usage\n"
    assert reg.load_doc("demo", "guide.md") is None

    ok, invalid = reg.validate_docs("demo", ["docs/guide.md", "usage.md"])
    assert ok is True
    assert invalid == []

    ok, invalid = reg.validate_docs("demo", ["guide.md"])
    assert ok is False
    assert invalid == ["guide.md"]


def test_skill_registry_refresh_detects_doc_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skills_root = tmp_path / "skills"
    _write_skill_with_docs(skills_root, "demo")
    monkeypatch.setenv("LUCKBOT_SKILLS_DIR", str(skills_root))

    reg = SkillRegistry()
    assert reg.refresh_if_changed() is True
    assert reg.refresh_if_changed() is False

    new_doc = skills_root / "demo" / "extra" / "new-note.md"
    new_doc.write_text("# new\n\nbody\n", encoding="utf-8")

    assert reg.refresh_if_changed() is True
    assert "extra/new-note.md" in reg.doc_names("demo")


def test_skill_registry_discovers_project_skills_independent_of_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_skill_with_docs(tmp_path / ".luckbot" / "skills", "project-demo")
    monkeypatch.setenv("LUCKBOT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("LUCKBOT_SKILLS_DIR", raising=False)
    monkeypatch.chdir(tmp_path.parent)

    reg = SkillRegistry()
    assert reg.refresh_if_changed() is True

    skill = reg.resolve("project-demo")
    assert skill is not None
    assert skill.name == "project-demo"
