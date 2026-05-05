"""Memory Flush subagent：memory 领域适配层 + 通用 subagent runtime。"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from luckbot.domains.memory.embeddings import EmbeddingProvider
from luckbot.domains.memory.index_db import MemoryIndex
from luckbot.domains.memory.memory_tools import (
    MAX_MEMORY_WRITE_CHARS,
    NO_MEMORIES_TOKEN,
    build_memory_get_tool,
    build_memory_search_tool,
)
from luckbot.domains.memory.paths import (
    is_allowed_memory_read_rel,
    is_under_memory_root,
    resolve_memory_read_path,
    resolve_memory_write_path,
)
from luckbot.domains.memory.types import MemoryPaths
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.subagent import SubagentRunRequest, run_subagent

logger = logging.getLogger(__name__)


MEMORY_FLUSH_AGENT_SYSTEM = (
    "你是记忆整理助手，运行在「压缩主对话上下文之前」的步骤。\n"
    "可用工具：memory_search、memory_get、memory_read、memory_write、memory_edit。\n"
    "**本轮 flush 的首要落盘目标**是系统消息给出的 **`memory/YYYY-MM-DD.md`**（与当日 UTC 日期同名的单文件，"
    "逻辑路径形如 `memory/2026-04-12.md`，具体文件名见下方的「本回默认日文件」）。\n"
    "建议：需要对照既有笔记时先用 memory_search / memory_get / memory_read；"
    "再把值得持久保存的内容写入该日文件（可 append 到文末或新建该文件）。\n"
    "仅在确有必要时才额外更新 MEMORY.md 或其它 `memory/*.md`（例如合并全局偏好、修正已有日文件中的待办状态）。\n"
    "不要编造对话中未出现的内容；不要输出与落盘无关的长篇说明。\n"
    f"若没有任何需要持久化的信息，最终回复必须且只能为：{NO_MEMORIES_TOKEN}"
)


def _build_memory_flush_read_tool(paths: MemoryPaths) -> Any:
    @tool
    async def memory_read(
        rel_path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> str:
        """读取记忆目录下的 Markdown（逻辑路径如 MEMORY.md、memory/2026-04-11.md）。offset 为起始行号（1-based），limit 为行数（0=读到末尾）。"""
        rel = rel_path.strip().replace("\\", "/")
        if not is_allowed_memory_read_rel(rel, paths):
            return f"[错误] path 不允许: {rel_path}"
        p = resolve_memory_read_path(rel, paths)
        if p is None or not p.is_file():
            return f"[错误] 文件不存在: {rel_path}"
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
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
            return f"[错误] offset {start} 超出总行数 {total}"
        width = len(str(end))
        numbered: list[str] = []
        for i in range(start - 1, end):
            numbered.append(f"{str(i + 1).rjust(width)}|{lines[i]}")
        return f"[{rel}] 第 {start}-{end} 行 / 共 {total} 行\n" + "\n".join(numbered)

    return memory_read


def _build_memory_flush_write_tool(paths: MemoryPaths) -> Any:
    @tool
    async def memory_write(
        rel_path: str,
        content: str,
        mode: str = "overwrite",
    ) -> str:
        """写入 memory白名单内的 .md。mode 为 overwrite（整文件替换）或 append（追加到文末）。"""
        rel = rel_path.strip().replace("\\", "/")
        p = resolve_memory_write_path(rel, paths)
        if p is None:
            return f"[错误] path 不允许: {rel_path}"
        if not is_under_memory_root(p, paths):
            return "[错误] 路径越界"
        if len(content) > MAX_MEMORY_WRITE_CHARS:
            return f"[错误] 内容过长（>{MAX_MEMORY_WRITE_CHARS} 字符）"
        m = (mode or "overwrite").strip().lower()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if m == "append":
                had_content = p.exists() and p.stat().st_size > 0
                with p.open("a", encoding="utf-8") as f:
                    if had_content and not content.startswith("\n"):
                        f.write("\n")
                    f.write(content)
            elif m == "overwrite":
                p.write_text(content, encoding="utf-8")
            else:
                return f"[错误] 未知 mode: {mode}（使用 overwrite 或 append）"
        except OSError as exc:
            return f"[错误] 写入失败: {exc}"
        return f"[OK] 已写入 {rel}"

    return memory_write


def _build_memory_flush_edit_tool(paths: MemoryPaths) -> Any:
    @tool
    async def memory_edit(
        rel_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        """在允许的 md 文件内做唯一子串替换（replace_all=true 时替换所有出现处）。"""
        rel = rel_path.strip().replace("\\", "/")
        p = resolve_memory_write_path(rel, paths)
        if p is None:
            return f"[错误] path 不允许: {rel_path}"
        if not is_under_memory_root(p, paths):
            return "[错误] 路径越界"
        if not p.is_file():
            return f"[错误] 文件不存在: {rel_path}"
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"[错误] 读取失败: {exc}"
        count = content.count(old_string)
        if count == 0:
            return "[错误] old_string 未找到，请核对全文（含空格与换行）。"
        if count > 1 and not replace_all:
            return f"[错误] old_string 出现 {count} 次，请加长上下文或设 replace_all=true"
        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced = 1
        try:
            p.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            return f"[错误] 写入失败: {exc}"
        return f"[OK] {rel}：已替换 {replaced} 处"

    return memory_edit


class MemoryFlushToolsPlugin(LuckbotPlugin):
    """仅注册 Flush 白名单工具，不挂 before_llm_call。"""

    name = "memory_flush_tools"
    version = "0.1.0"

    def __init__(
        self,
        paths: MemoryPaths,
        index: MemoryIndex,
        provider: EmbeddingProvider,
    ) -> None:
        self._paths = paths
        self._index = index
        self._provider = provider

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_tool(
            "memory_search",
            build_memory_search_tool(self._index, self._provider),
        )
        ctx.register_tool("memory_get", build_memory_get_tool(self._paths))
        ctx.register_tool("memory_read", _build_memory_flush_read_tool(self._paths))
        ctx.register_tool("memory_write", _build_memory_flush_write_tool(self._paths))
        ctx.register_tool("memory_edit", _build_memory_flush_edit_tool(self._paths))


def _build_memory_flush_system_prompt(day: str) -> str:
    return (
        MEMORY_FLUSH_AGENT_SYSTEM
        + f"\n\n**本回默认日文件（逻辑路径）**：memory/{day}.md\n"
        f"静默无写入时回复：{NO_MEMORIES_TOKEN}"
    )


def _build_memory_flush_user_message(transcript: str, day: str) -> str:
    return (
        f"以下是对话摘录。请**优先**把值得长期保留的信息写入 **memory/{day}.md** "
        "（与系统消息中的默认日文件一致；可用 memory_write 的 append 或 overwrite）。\n\n---\n"
        + transcript
    )


async def run_memory_flush_agent(
    transcript: str,
    memory_paths: MemoryPaths,
    index: MemoryIndex,
    provider: EmbeddingProvider,
    *,
    max_steps: int,
    day: str,
) -> tuple[str, list[Any]]:
    """执行 memory flush subagent；结束后 ``index.sync()``。"""
    request = SubagentRunRequest(
        system_prompt=_build_memory_flush_system_prompt(day),
        messages=[
            HumanMessage(content=_build_memory_flush_user_message(transcript, day))
        ],
        max_steps=max_steps,
        plugins=[MemoryFlushToolsPlugin(memory_paths, index, provider)],
    )
    result = await run_subagent(request)
    try:
        await index.sync()
    except Exception:
        logger.exception("flush 后 memory sync 失败")
    return result.final_text, result.messages


__all__ = [
    "MEMORY_FLUSH_AGENT_SYSTEM",
    "MemoryFlushToolsPlugin",
    "run_memory_flush_agent",
]
