"""write 工具：写入文件内容，自动创建父目录。"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import tool


def build_write_tool() -> Any:
    """构建文件写入工具。"""

    @tool(
        description=(
            "将文本写入文件；若父目录不存在会自动创建。"
            "若文件已存在则覆盖。"
            "不要用 bash 的 echo/重定向代替本工具创建文件。"
        ),
    )
    def write(path: str, content: str) -> str:
        """将 content 写入 path。自动创建父目录。"""
        path = os.path.expanduser(path)

        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            created = not os.path.exists(path)
            with open(path, "w", encoding="utf-8") as f:
                written = f.write(content)

            action = "新建" if created else "覆盖"
            return f"[OK] {path}（{action}，{written} 字节）"
        except PermissionError:
            return f"[错误] 权限不足: {path}"
        except Exception as exc:
            return f"[错误] 写入失败: {exc}"

    return write
