"""bash 工具：通过 subprocess 执行 shell 命令。

简单命令走 shell=False（更安全），含管道/重定向的走 shell=True。
安全分类由 SafetyConfig 驱动，blocked 命令直接拒绝。
"""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Any

from langchain_core.tools import tool

from .safety import SafetyConfig, classify_command, describe_block_reason, truncate_output

_SHELL_METACHARACTERS = frozenset("|><&$`(")


def build_bash_tool(safety: SafetyConfig) -> Any:
    """构建带安全策略的 bash 工具。"""

    @tool(
        description=(
            "执行 shell 命令并返回标准输出/标准错误。"
            "用于终端类操作：git、构建、测试、在项目目录运行脚本等。"
            "文件读写与搜索请优先使用 read、write、edit、grep、ls。"
        ),
    )
    def bash(command: str, timeout: int = 0, cwd: str = "") -> str:
        """执行 bash 命令。timeout 为秒数（0 表示使用默认值），cwd 为工作目录。"""
        effective_timeout = timeout if timeout > 0 else safety.bash_timeout
        effective_cwd = cwd or os.getcwd()

        classification = classify_command(command, safety)
        if classification == "blocked":
            reason = describe_block_reason(command, safety)
            return f"[已拦截] 安全策略拦截: {reason}\n命令: {command}"

        needs_shell = any(c in command for c in _SHELL_METACHARACTERS)

        try:
            if needs_shell:
                proc = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    cwd=effective_cwd,
                )
            else:
                tokens = shlex.split(command)
                tokens = _expand_tilde(tokens)
                proc = subprocess.run(
                    tokens,
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    cwd=effective_cwd,
                )
        except subprocess.TimeoutExpired:
            return f"[超时] 命令执行超时（{effective_timeout}秒）: {command}"
        except FileNotFoundError:
            base = shlex.split(command)[0] if command.strip() else command
            return f"[错误] 命令不存在: {base}"
        except Exception as exc:
            return f"[错误] 执行失败: {exc}"

        parts: list[str] = []
        if proc.stdout:
            parts.append(proc.stdout)
        if proc.stderr:
            parts.append(f"[标准错误]\n{proc.stderr}")
        if proc.returncode != 0:
            parts.append(f"[退出码: {proc.returncode}]")

        output = "\n".join(parts) if parts else "（无输出）"
        return truncate_output(output, safety.bash_max_output)

    return bash


def _expand_tilde(tokens: list[str]) -> list[str]:
    """展开参数中的 ~ 为用户主目录。"""
    home = os.path.expanduser("~")
    result = []
    for t in tokens:
        if t == "~":
            result.append(home)
        elif t.startswith("~/"):
            result.append(home + t[1:])
        else:
            result.append(t)
    return result
