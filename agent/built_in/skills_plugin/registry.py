"""SkillRegistry：多根目录发现、SKILL.md 解析、自动文档扫描、指纹缓存。

发现优先级（同名 skill 项目级覆盖用户级）：
  1. .luckbot/skills/          （项目级）
  2. ~/.luckbot/skills/         （用户级）
  3. LUCKBOT_SKILLS_DIR 环境变量（逗号分隔多个路径）
"""

from __future__ import annotations

import glob as _glob
import logging
import os
from typing import Any

import frontmatter

from .types import SkillDoc, SkillInfo

logger = logging.getLogger(__name__)

_Fingerprint = tuple[tuple[str, float], ...]

_DEFAULT_SCAN_PATHS = [
    ".luckbot/skills",
    os.path.expanduser("~/.luckbot/skills"),
]


class SkillRegistry:
    """负责 SKILL.md 文件的发现、解析和缓存管理。"""

    def __init__(self) -> None:
        self._scan_paths = self._build_scan_paths()
        self._fingerprint: _Fingerprint | None = None
        self._skills: dict[str, SkillInfo] = {}

    # -- 公共 API -------------------------------------------------------

    def all_skills(self) -> list[SkillInfo]:
        return list(self._skills.values())

    def resolve(self, name: str) -> SkillInfo | None:
        return self._skills.get(name)

    def load_doc(self, skill_name: str, doc_name: str) -> str | None:
        """读取指定 skill 的某个参考文档内容。"""
        skill = self._skills.get(skill_name)
        if skill is None:
            return None
        for doc in skill.docs:
            if doc.name == doc_name:
                try:
                    with open(doc.path, "r", encoding="utf-8") as f:
                        return f.read()
                except OSError:
                    logger.warning("无法读取文档 %s", doc.path)
                    return None
        return None

    def refresh_if_changed(self) -> bool:
        """检查指纹，有变化则重新扫描。返回是否刷新。"""
        fp = self._compute_fingerprint()
        if fp == self._fingerprint:
            return False
        self._fingerprint = fp
        self._scan_all()
        return True

    # -- 扫描路径 -------------------------------------------------------

    @staticmethod
    def _build_scan_paths() -> list[str]:
        paths = list(_DEFAULT_SCAN_PATHS)
        extra = os.getenv("LUCKBOT_SKILLS_DIR", "")
        if extra.strip():
            for p in extra.split(","):
                p = p.strip()
                if p:
                    paths.append(os.path.expanduser(p))
        return paths

    # -- 指纹计算 -------------------------------------------------------

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

    # -- 全量扫描 -------------------------------------------------------

    def _scan_all(self) -> None:
        seen: dict[str, SkillInfo] = {}
        for base in self._scan_paths:
            pattern = os.path.join(base, "**", "SKILL.md")
            for filepath in _glob.glob(pattern, recursive=True):
                try:
                    info = _parse_skill_file(filepath)
                    if info is None:
                        continue
                    if info.name not in seen:
                        seen[info.name] = info
                except Exception:
                    logger.exception("解析 skill 文件失败: %s", filepath)
        self._skills = seen
        logger.info("已扫描 %d 个 skill", len(seen))


# -- 解析辅助 -----------------------------------------------------------


def _parse_skill_file(filepath: str) -> SkillInfo | None:
    """解析单个 SKILL.md，返回 SkillInfo（含自动发现的参考文档）。"""
    post = frontmatter.load(filepath)
    name = post.metadata.get("name")
    description = post.metadata.get("description")
    if not name or not description:
        logger.warning("SKILL.md 缺少 name 或 description: %s", filepath)
        return None

    base_dir = os.path.dirname(filepath)
    docs = _scan_docs(base_dir)
    when_to_use = post.metadata.get("when_to_use")

    return SkillInfo(
        name=name,
        description=description,
        content=post.content,
        location=filepath,
        base_dir=base_dir,
        when_to_use=when_to_use,
        docs=docs,
    )


def _scan_docs(skill_dir: str) -> list[SkillDoc]:
    """自动扫描 skill 目录下的 .md 文件（排除 SKILL.md）作为参考文档。"""
    docs: list[SkillDoc] = []
    for root, _dirs, files in os.walk(skill_dir):
        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue
            if fname.upper() == "SKILL.MD":
                continue
            full_path = os.path.join(root, fname)
            desc = _extract_doc_description(full_path, fname)
            docs.append(SkillDoc(name=fname, description=desc, path=full_path))
    return docs


def _extract_doc_description(filepath: str, fallback: str) -> str:
    """从文件首行提取描述（取 # 标题或第一行非空文本），失败则用文件名。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    return line.lstrip("#").strip()
                return line[:120]
    except OSError:
        pass
    return fallback
