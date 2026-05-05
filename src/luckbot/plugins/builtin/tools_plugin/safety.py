"""Bash 命令安全分类与拦截。

三层安全模型：
  Layer 1 — 分类：command → read / write / blocked
  Layer 2 — 策略：read 直接执行，write 直接执行（可配置），blocked 拒绝
  Layer 3 — before_tool_call hook 兜底（由 ToolsPlugin 注册）
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Literal

CommandClass = Literal["read", "write", "blocked"]

# ── 默认名单 ──────────────────────────────────────────────────────

_DEFAULT_SAFE: set[str] = {
    "ls", "dir", "cat", "head", "tail", "less", "more", "wc",
    "file", "stat", "tree", "du", "df",
    "grep", "egrep", "fgrep", "rg", "ag",
    "sed", "awk", "cut", "sort", "uniq", "diff", "cmp",
    "find", "locate",
    "pwd", "whoami", "hostname", "uname", "date", "cal", "uptime",
    "env", "printenv", "echo", "type", "which", "whereis",
    "id", "groups",
    "python", "python3", "pip", "pip3",
    "node", "npm", "npx", "bun",
    "git",
}

_DEFAULT_BLOCKED: set[str] = {
    "sudo", "su", "doas",
    "dd", "mkfs", "fdisk", "parted",
    "shutdown", "reboot", "init", "systemctl",
    "shred",
    "nc", "netcat", "telnet",
    "crontab", "at", "batch",
}

_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-[^\s]*r[^\s]*f.*\s+/\s*$", "rm -rf / 可能删除整个系统"),
    (r"rm\s+-[^\s]*r[^\s]*f.*\s+~", "rm -rf ~ 可能删除用户主目录"),
    (r"chmod\s+777\s", "chmod 777 权限过于宽松"),
    (r">\s*/dev/sd", "直接写入块设备"),
    (r"mkfs\.", "格式化文件系统"),
]


@dataclass
class SafetyConfig:
    """可配置的安全策略。"""

    safe_commands: set[str] = field(default_factory=lambda: set(_DEFAULT_SAFE))
    blocked_commands: set[str] = field(default_factory=lambda: set(_DEFAULT_BLOCKED))
    bash_timeout: int = 30
    bash_max_output: int = 50_000
    # sandbox_run：默认可跑更久、输出可略大（复杂脚本日志）
    sandbox_timeout: int = 120
    sandbox_max_output: int = 100_000
    allow_write_tools: bool = True

    # git 子命令中只有这些算只读
    _git_read_only: frozenset[str] = field(
        default=frozenset({
            "status", "log", "branch", "remote", "diff", "show",
            "ls-files", "ls-tree", "rev-parse", "describe", "tag",
            "stash", "reflog",
        }),
        repr=False,
    )


def classify_command(command: str, cfg: SafetyConfig) -> CommandClass:
    """将 bash 命令分为 read / write / blocked 三类。"""

    for pattern, _ in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return "blocked"

    tokens = _safe_split(command)
    if not tokens:
        return "write"

    base = os.path.basename(tokens[0])

    if base in cfg.blocked_commands:
        return "blocked"

    if base in cfg.safe_commands:
        if base == "git":
            return _classify_git(tokens, cfg)
        if ">" in command:
            return "write"
        return "read"

    if "|" in command:
        return _classify_pipeline(command, cfg)

    return "write"


def describe_block_reason(command: str, cfg: SafetyConfig) -> str:
    """如果命令被 blocked，返回原因说明。"""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return reason

    tokens = _safe_split(command)
    if tokens:
        base = os.path.basename(tokens[0])
        if base in cfg.blocked_commands:
            return f"命令 {base} 在黑名单中"

    return "未知原因"


def truncate_output(text: str, max_chars: int) -> str:
    """截断过长的输出，保留头尾。"""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n... [已截断，原始长度 {len(text)} 字符] ...\n\n"
        + text[-half:]
    )


# ── 内部辅助 ──────────────────────────────────────────────────────

def _safe_split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _classify_git(tokens: list[str], cfg: SafetyConfig) -> CommandClass:
    if len(tokens) > 1 and tokens[1] in cfg._git_read_only:
        return "read"
    return "write"


def _classify_pipeline(command: str, cfg: SafetyConfig) -> CommandClass:
    """管道中每段都是安全命令 → read；否则 → write。"""
    for part in command.split("|"):
        part_tokens = _safe_split(part.strip())
        if not part_tokens:
            continue
        base = os.path.basename(part_tokens[0])
        if base in cfg.blocked_commands:
            return "blocked"
        if base not in cfg.safe_commands:
            return "write"
    return "read"
