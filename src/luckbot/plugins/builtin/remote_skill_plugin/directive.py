"""兼容单次远程 skill 触发的轻量文本协议。"""

from __future__ import annotations

import re

from luckbot.domains.skills.types import RemoteSkillRequest

_USE_SKILL_PAREN_RE = re.compile(r"^\s*\[use skill\]\(([^)]+)\)\s*(.*)$", re.IGNORECASE | re.DOTALL)
_USE_SKILL_BRACKET_RE = re.compile(r"^\s*\[use skill\s+([^\]]+)\]\s*(.*)$", re.IGNORECASE | re.DOTALL)


def extract_remote_skill_directive(text: str) -> tuple[str, RemoteSkillRequest | None]:
    """提取前缀 skill 指令，返回去指令后的文本与 skill 请求。"""
    for pattern in (_USE_SKILL_PAREN_RE, _USE_SKILL_BRACKET_RE):
        match = pattern.match(text)
        if not match:
            continue
        skill_name = match.group(1).strip()
        remainder = match.group(2).strip()
        if not skill_name:
            break
        return remainder, RemoteSkillRequest(skill_name=skill_name, source="inline")
    return text, None
