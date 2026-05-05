"""ls 工具：列出目录内容及基本属性。"""

from __future__ import annotations

import os
import stat
from typing import Any

from langchain_core.tools import tool


def build_ls_tool() -> Any:
    """构建目录列表工具。"""

    @tool(
        description=(
            "列出指定路径下的文件与子目录。"
            "返回名称、类型（文件/目录/符号链接）及大致大小。"
            "不要用 bash 的 ls 代替本工具。"
        ),
    )
    def ls(path: str = ".") -> str:
        """列出目录内容。path 为目标路径，默认当前目录。"""
        path = os.path.expanduser(path)

        if not os.path.exists(path):
            return f"[错误] 路径不存在: {path}"
        if not os.path.isdir(path):
            return f"[错误] 不是目录: {path}"

        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return f"[错误] 权限不足: {path}"

        if not entries:
            return f"[{path}]（空目录）"

        lines: list[str] = [f"[{path}] 共 {len(entries)} 项"]
        for name in entries:
            full = os.path.join(path, name)
            try:
                st = os.lstat(full)
                if stat.S_ISLNK(st.st_mode):
                    target = os.readlink(full)
                    lines.append(f"  {name} -> {target}  （符号链接）")
                    continue
                elif stat.S_ISDIR(st.st_mode):
                    kind = "目录"
                    size_str = ""
                else:
                    kind = "文件"
                    size_str = _human_size(st.st_size)
                lines.append(f"  {name}  （{kind}{'，' + size_str if size_str else ''}）")
            except OSError:
                lines.append(f"  {name}  （无法读取属性）")

        return "\n".join(lines)

    return ls


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f}TB"
