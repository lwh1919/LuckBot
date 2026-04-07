"""内置 Skills 插件：扫描 SKILL.md 并注册 ``skill`` 工具。

使用指纹缓存（glob + mtime）：目录未变时每次 run 仅少量 stat 开销。
"""

from __future__ import annotations

import glob as _glob
import logging
import os
from dataclasses import dataclass
from typing import Any

import frontmatter
from langchain_core.tools import tool

from agent.plugin.base import LuckbotPlugin, PluginContext
from agent.plugin.hooks import BeforeRunInput, BeforeRunResult

logger = logging.getLogger(__name__)

_Fingerprint = tuple[tuple[str, float], ...]

_DEFAULT_SCAN_PATHS = [
    ".luckbot/skills",
    os.path.expanduser("~/.luckbot/skills"),
]


@dataclass
class SkillInfo:
    name: str
    description: str
    content: str
    location: str


class SkillsPlugin(LuckbotPlugin):
    """发现 SKILL.md 并为 Agent 注册 ``skill`` 工具。"""

    name = "luckbot/built-in-skills"
    version = "0.1.0"

    def __init__(self, scan_paths: list[str] | None = None) -> None:
        if scan_paths is not None:
            self._scan_paths = list(scan_paths)
        else:
            self._scan_paths = list(_DEFAULT_SCAN_PATHS)
        self._fingerprint: _Fingerprint | None = None
        self._cached_tool: Any | None = None

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_run", self._before_run)

    # -- hook ----------------------------------------------------------------

    async def _before_run(self, inp: BeforeRunInput) -> BeforeRunResult | None:
        fp = self._compute_fingerprint()

        if fp == self._fingerprint and self._cached_tool is not None:
            return BeforeRunResult(tools={**inp.tools, "skill": self._cached_tool})

        skills = self._scan_skills()
        self._fingerprint = fp

        if not skills:
            self._cached_tool = None
            return None

        self._cached_tool = _build_skill_tool(skills)
        return BeforeRunResult(tools={**inp.tools, "skill": self._cached_tool})

    # -- 指纹 ---------------------------------------------------------------

    def _compute_fingerprint(self) -> _Fingerprint:
        entries: list[tuple[str, float]] = []
        for base in self._scan_paths:
            pattern = os.path.join(base, "**", "SKILL.md")
            for f in _glob.glob(pattern, recursive=True):
                try:
                    entries.append((f, os.stat(f).st_mtime))
                except OSError:
                    pass
        return tuple(sorted(entries))

    # -- 扫描 ---------------------------------------------------------------

    def _scan_skills(self) -> list[SkillInfo]:
        skills: list[SkillInfo] = []
        for base in self._scan_paths:
            pattern = os.path.join(base, "**", "SKILL.md")
            for filepath in _glob.glob(pattern, recursive=True):
                try:
                    si = _parse_skill_file(filepath)
                    if si is not None:
                        skills.append(si)
                except Exception:
                    logger.exception("解析 skill 文件失败 %s", filepath)
        logger.info("已扫描 %d 个 skill", len(skills))
        return skills


# -- 辅助函数 -------------------------------------------------------------


def _parse_skill_file(filepath: str) -> SkillInfo | None:
    post = frontmatter.load(filepath)
    name = post.metadata.get("name")
    description = post.metadata.get("description")
    if not name or not description:
        logger.warning("SKILL.md 缺少 name 或 description: %s", filepath)
        return None
    return SkillInfo(
        name=name,
        description=description,
        content=post.content,
        location=filepath,
    )


def _build_skill_tool(skills: list[SkillInfo]) -> Any:
    """构造 LangChain 工具：description 内嵌 skill 目录，与 Pulse 行为一致。"""
    skill_map = {s.name: s for s in skills}
    listing_lines: list[str] = []
    for skill_info in skills:
        listing_lines.append(f"  - {skill_info.name}: {skill_info.description}")
    listing = "\n".join(listing_lines)
    desc = (
        "加载 skill 以获取分步说明。\n"
        f"可用 skill 列表:\n{listing}"
    )

    @tool(description=desc)
    def skill(name: str) -> str:  # noqa: ARG001 — name 通过闭包使用
        """返回指定 skill 的全文 Markdown。"""
        info = skill_map.get(name)
        if info is None:
            return f"未找到名为「{name}」的 skill。"
        return info.content

    return skill
