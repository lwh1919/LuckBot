"""Skill 系统数据类定义。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SkillDoc:
    """自动扫描到的参考文档（skill 目录下排除 SKILL.md 的 .md 文件）。"""

    name: str
    description: str
    path: str


@dataclass
class SkillInfo:
    """从 SKILL.md 解析出的技能元信息。"""

    name: str
    description: str
    content: str
    location: str
    base_dir: str
    when_to_use: str | None
    docs: list[SkillDoc] = field(default_factory=list)


@dataclass
class ExecResult:
    """skill_run 沙箱执行结果。"""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    output_files: dict[str, str] = field(default_factory=dict)


@dataclass
class SkillState:
    """CQRS 临时状态，每次 agent_loop 调用时重置。"""

    loaded: dict[str, bool] = field(default_factory=dict)
    selected_docs: dict[str, list[str]] = field(default_factory=dict)
