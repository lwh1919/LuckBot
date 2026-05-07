"""Skill prompt 构造。"""

from __future__ import annotations

from .registry import SkillRegistry
from .types import SkillState


def build_skill_prompt(registry: SkillRegistry, state: SkillState) -> str:
    parts: list[str] = []
    parts.extend(_build_skill_overview(registry))
    parts.extend(_build_loaded_skills(registry, state))
    parts.extend(_build_selected_docs(registry, state))
    return "\n\n".join(parts)


def _build_skill_overview(registry: SkillRegistry) -> list[str]:
    skills = registry.all_skills()
    if not skills:
        return []

    lines: list[str] = []
    for skill in skills:
        entry = f"  - {skill.name}: {skill.description[:200]}"
        if skill.when_to_use:
            entry += f"\n    适用场景: {skill.when_to_use}"
        lines.append(entry)
    listing = "\n".join(lines)
    return [
        f"可用技能列表:\n{listing}\n"
        f"请使用 skill_load 加载技能，并按技能说明操作。"
    ]


def _build_loaded_skills(
    registry: SkillRegistry,
    state: SkillState,
) -> list[str]:
    parts: list[str] = []
    for name in state.loaded:
        skill = registry.resolve(name)
        if skill is None:
            continue
        parts.append(f"--- 已加载技能: {skill.name} ---\n{skill.content}")
        if skill.docs:
            doc_list = "\n".join(
                f"  - {doc.name}: {doc.description}" for doc in skill.docs
            )
            parts.append(f"可参考文档（用 skill_select_docs 加载）:\n{doc_list}")
    return parts


def _build_selected_docs(
    registry: SkillRegistry,
    state: SkillState,
) -> list[str]:
    parts: list[str] = []
    for skill_name, doc_names in state.selected_docs.items():
        for doc_name in doc_names:
            content = registry.load_doc(skill_name, doc_name)
            if content is None:
                continue
            parts.append(f"--- 参考文档: {skill_name}/{doc_name} ---\n{content}")
    return parts
