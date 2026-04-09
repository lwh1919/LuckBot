"""grep 工具：代码搜索，优先使用 ripgrep (rg)，fallback 到 Python re。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any

from langchain_core.tools import tool

_MAX_RESULTS = 200


def build_grep_tool() -> Any:
    """构建代码搜索工具。"""

    rg_path = shutil.which("rg")

    @tool(
        description=(
            "按正则搜索文件内容。"
            "返回匹配行，含路径与行号。"
            "支持 glob_filter（如 *.py）与 file_type（如 py，需系统已安装 rg）。"
            "不要用 bash 的 grep/rg 代替本工具。"
        ),
    )
    def grep(
        pattern: str,
        path: str = ".",
        glob_filter: str = "",
        file_type: str = "",
        context_lines: int = 0,
    ) -> str:
        """搜索文件内容。path 为搜索根目录，glob_filter 如 '*.py'，file_type 如 'py'。"""
        search_path = os.path.expanduser(path)

        if not os.path.exists(search_path):
            return f"[错误] 路径不存在: {search_path}"

        if rg_path:
            return _rg_search(
                rg_path, pattern, search_path,
                glob_filter, file_type, context_lines,
            )
        return _py_search(pattern, search_path, glob_filter)

    return grep


def _rg_search(
    rg: str,
    pattern: str,
    path: str,
    glob_filter: str,
    file_type: str,
    context_lines: int,
) -> str:
    cmd: list[str] = [
        rg, "--no-heading", "--line-number", "--color=never",
        f"--max-count={_MAX_RESULTS}",
    ]
    if context_lines > 0:
        cmd.append(f"-C{context_lines}")
    if glob_filter:
        cmd.extend(["--glob", glob_filter])
    if file_type:
        cmd.extend(["--type", file_type])
    cmd.extend(["--", pattern, path])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "[超时] 搜索超时（30秒）"
    except Exception as exc:
        return f"[错误] rg 执行失败: {exc}"

    if proc.returncode == 1:
        return "（无匹配）"
    if proc.returncode not in (0, 1):
        return f"[错误] rg 退出码 {proc.returncode}: {proc.stderr.strip()}"

    lines = proc.stdout.strip().split("\n")
    if len(lines) > _MAX_RESULTS:
        lines = lines[:_MAX_RESULTS]
        lines.append(f"...（结果已截断，最多 {_MAX_RESULTS} 条）")

    return "\n".join(lines) if lines and lines[0] else "（无匹配）"


def _py_search(pattern: str, path: str, glob_filter: str) -> str:
    """纯 Python fallback（无 rg 时使用）。"""
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"[错误] 正则表达式错误: {exc}"

    import fnmatch

    results: list[str] = []
    count = 0

    for root, _, files in os.walk(path):
        if _should_skip_dir(root):
            continue
        for fname in files:
            if glob_filter and not fnmatch.fnmatch(fname, glob_filter):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(f"{fpath}:{lineno}:{line.rstrip()}")
                            count += 1
                            if count >= _MAX_RESULTS:
                                results.append(f"...（结果已截断，最多 {_MAX_RESULTS} 条）")
                                return "\n".join(results)
            except (OSError, UnicodeDecodeError):
                continue

    return "\n".join(results) if results else "（无匹配）"


def _should_skip_dir(dirpath: str) -> bool:
    name = os.path.basename(dirpath)
    return name in {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    }
