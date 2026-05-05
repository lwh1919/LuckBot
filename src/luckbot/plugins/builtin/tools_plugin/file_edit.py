"""edit 工具：精确字符串替换编辑文件。"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import tool


def build_edit_tool() -> Any:
    """构建文件编辑工具。"""

    @tool(
        description=(
            "通过精确字符串替换编辑文件。"
            "old_string 须在文件中唯一匹配（可带前后文以定位）；"
            "replace_all=true 时替换所有出现处。"
        ),
    )
    def edit(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        """精确替换文件中的字符串。old_string 必须在文件中唯一匹配（除非 replace_all=True）。"""
        path = os.path.expanduser(path)

        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        if os.path.isdir(path):
            return f"[错误] 路径是目录: {path}"

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except PermissionError:
            return f"[错误] 权限不足: {path}"
        except Exception as exc:
            return f"[错误] 读取失败: {exc}"

        count = content.count(old_string)
        if count == 0:
            return f"[错误] old_string 在文件中未找到。请检查内容是否精确匹配（含缩进和空格）。"
        if count > 1 and not replace_all:
            return (
                f"[错误] old_string 在文件中出现 {count} 次，无法唯一定位。"
                f"请提供更多上下文使其唯一，或设置 replace_all=true。"
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced = 1

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as exc:
            return f"[错误] 写入失败: {exc}"

        return f"[OK] {path}：已替换 {replaced} 处"

    return edit
