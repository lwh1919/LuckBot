"""read 工具：读取文件内容，支持行号范围。"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import tool


def build_read_tool() -> Any:
    """构建文件读取工具。"""

    @tool(
        description=(
            "读取文件内容并带行号返回。"
            "可选 offset：起始行号（从 1 计）；limit：读取行数（0 表示读到末尾）。"
            "不要用 bash 的 cat/head/tail 代替本工具。"
        ),
    )
    def read(path: str, offset: int = 0, limit: int = 0) -> str:
        """读取文件内容。offset 为起始行号（1-based），limit 为读取行数（0=全部）。"""
        path = os.path.expanduser(path)

        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        if os.path.isdir(path):
            return f"[错误] 路径是目录，请使用 ls 工具: {path}"

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except PermissionError:
            return f"[错误] 权限不足: {path}"
        except Exception as exc:
            return f"[错误] 读取失败: {exc}"

        total = len(lines)
        if total == 0:
            return "（空文件）"

        start = max(1, offset) if offset > 0 else 1
        if limit > 0:
            end = min(start + limit - 1, total)
        else:
            end = total

        if start > total:
            return f"[错误] offset {start} 超出文件总行数 {total}"

        numbered: list[str] = []
        width = len(str(end))
        for i in range(start - 1, end):
            lineno = str(i + 1).rjust(width)
            numbered.append(f"{lineno}|{lines[i].rstrip()}")

        header = f"[{path}] 第 {start}-{end} 行 / 共 {total} 行"
        return header + "\n" + "\n".join(numbered)

    return read
