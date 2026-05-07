"""解析单次 skill 激活文本指令。"""

from __future__ import annotations

import re

from .types import SkillActivationRequest

_USE_SKILL_PAREN_RE = re.compile(r"^\s*\[use skill\]\(([^)]+)\)\s*(.*)$", re.IGNORECASE | re.DOTALL)
_USE_SKILL_BRACKET_RE = re.compile(r"^\s*\[use skill\s+([^\]]+)\]\s*(.*)$", re.IGNORECASE | re.DOTALL)


def parse_skill_activation_directive(
    text: str,
) -> tuple[str, SkillActivationRequest | None]:
    """提取前缀 skill 指令，返回去指令后的文本与本轮激活请求。"""
    for pattern in (_USE_SKILL_PAREN_RE, _USE_SKILL_BRACKET_RE):
        match = pattern.match(text)
        if not match:
            continue
        skill_name = match.group(1).strip()
        remainder = match.group(2).strip()
        if not skill_name:
            break
        return remainder, SkillActivationRequest(skill_name=skill_name)
    return text, None
