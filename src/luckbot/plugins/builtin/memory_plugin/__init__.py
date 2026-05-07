"""记忆索引插件：memory_search / memory_get、同步索引、可选文件监听与上下文压缩。

依赖 agent.memory.*完成路径、SQLite 索引、混合检索；本文件负责注册工具与钩子，
把「长期记忆」接入 LuckBot 插件生命周期。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from langchain_core.tools import tool

from luckbot.core.config.env_parse import env_int
from luckbot.core.observability import (
    increment_counter,
    log_exception,
    record_exception,
    start_span,
)
from luckbot.core.runtime import current_runtime_context
from luckbot.domains.memory.compaction import maybe_compact_before_llm
from luckbot.domains.memory.embeddings import EmbeddingProvider, build_embedding_provider
from luckbot.domains.memory.index_db import MemoryIndex
from luckbot.domains.memory.memory_tools import build_memory_get_tool, build_memory_search_tool
from luckbot.domains.memory.paths import ensure_memory_tree, resolve_memory_paths
from luckbot.domains.memory.session_memory import archive_last_session_to_markdown
from luckbot.domains.memory.types import MemoryPaths
from luckbot.domains.session import default_owner_id
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.plugin.hooks import (
    AfterRunInput,
    BeforeLLMCallInput,
    BeforeLLMCallResult,
    BeforeRunInput,
    BeforeRunResult,
)

logger = logging.getLogger(__name__)

# 注入系统提示，约束模型在需要时先检索再回答（与工具 schema 配合）
MEMORY_RECALL = (
    "## Memory Recall\n"
    "在回答涉及过往工作、决策、日期、人物、偏好、TODO 的问题前，必须先调用 memory_search，"
    "该工具会搜索长期记忆（MEMORY.md、memory/*.md、extra/*.md）；"
    "大多数情况下直接依据 search 返回的 snippet、score、source 回答即可；"
    "只有在需要回看记忆原文上下文、长文阅读、精确措辞确认或检索后仍低置信度时，再用 memory_get 拉取需要的行。"
    "memory_get 读取的是长期记忆 Markdown，不读取 raw session transcript。"
    "检索后仍不确定请说明已检索过。\n\n"
)


def _memory_enabled() -> bool:
    """LUCKBOT_MEMORY_ENABLED 非 0/false/no/off 时启用本插件。"""
    return os.getenv("LUCKBOT_MEMORY_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


class MemoryPlugin(LuckbotPlugin):
    """挂载记忆工具、索引生命周期与可选 md 热更新。"""

    name = "memory"
    version = "0.1.0"

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self._paths: MemoryPaths | None = None
        self._index: MemoryIndex | None = None
        self._provider: EmbeddingProvider | None = None
        self._embedding_dim: int | None = None
        self._runtime_state: dict[str, tuple[MemoryPaths, MemoryIndex, Any, Any]] = {}
        self._observer: Any = None  # watchdog.Observer，未安装或未启用时为 None
        self._watch_timer: threading.Timer | None = None

    async def initialize(self, ctx: PluginContext) -> None:
        if not _memory_enabled():
            return

        self._ctx = ctx
        prov = build_embedding_provider()
        dim_env = (os.getenv("LUCKBOT_MEMORY_EMBEDDING_DIM", "") or "").strip()
        if not dim_env:
            dim = None
        else:
            parsed = env_int("LUCKBOT_MEMORY_EMBEDDING_DIM", 0)
            dim = parsed if parsed > 0 else None
        self._provider = prov
        self._embedding_dim = dim
        ctx.register_tool("memory_search", self._build_owner_aware_memory_search_tool())
        ctx.register_tool("memory_get", self._build_owner_aware_memory_get_tool())
        ctx.register_service("memory_sync_now", self._sync_now)

        ctx.register_hook("before_run", self._before_run)
        ctx.register_hook("after_run", self._after_run)
        ctx.register_hook("before_llm_call", self._before_llm)

        self._activate_owner(None)

    def _start_watcher_if_needed(self) -> None:
        """监听 memory_root 下 .md 变更，防抖后触发 MemoryIndex.sync（需 watchdog）。"""
        if (
            os.getenv("LUCKBOT_MEMORY_WATCH", "1").strip().lower()
            in ("0", "false", "no", "off")
        ):
            return
        if not self._paths or not self._index:
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return

        mem_root = self._paths.memory_root
        index = self._index

        lock = threading.Lock()
        # 单元素列表：闭包内可修改引用，用于取消/重置 Timer
        timer_ref: list[threading.Timer | None] = [None]

        def debounced_sync() -> None:
            # 监听线程无 running loop时用 asyncio.run；已有 loop 时退化为新 loop
            try:
                asyncio.run(index.sync())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(index.sync())
                finally:
                    loop.close()

        def schedule() -> None:
            # 0.5s 内多次事件合并为一次 sync，避免编辑时连续写盘打爆索引
            with lock:
                if timer_ref[0]:
                    timer_ref[0].cancel()
                timer_ref[0] = threading.Timer(0.5, debounced_sync)
                timer_ref[0].start()

        class _H(FileSystemEventHandler):
            def on_modified(self, event: Any) -> None:
                if event.is_directory:
                    return
                if not str(event.src_path).lower().endswith(".md"):
                    return
                schedule()

            def on_created(self, event: Any) -> None:
                self.on_modified(event)

        obs = Observer()
        obs.schedule(_H(), mem_root, recursive=True)
        obs.start()
        self._observer = obs

    async def destroy(self, ctx: PluginContext) -> None:  # noqa: ARG002
        """插件卸载：停监听、关 SQLite。"""
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass
            self._observer = None
        for _owner_id, (_paths, index, _search, _get) in list(self._runtime_state.items()):
            index.close()
        self._runtime_state.clear()
        self._index = None
        self._paths = None

    def _activate_owner(self, owner_id: str | None) -> tuple[MemoryPaths, MemoryIndex, Any, Any]:
        effective_owner = default_owner_id(owner_id or "local")
        cached = self._runtime_state.get(effective_owner)
        if cached is None:
            with start_span(
                "memory.owner.activate",
                attributes={"luckbot.owner_id": effective_owner},
            ):
                paths = resolve_memory_paths(owner=effective_owner)
                ensure_memory_tree(paths)
                index = MemoryIndex(
                    paths,
                    provider=self._provider,
                    embedding_dim=self._embedding_dim,
                )
                search_tool = build_memory_search_tool(index, self._provider)
                get_tool = build_memory_get_tool(paths)
                cached = (paths, index, search_tool, get_tool)
                self._runtime_state[effective_owner] = cached
                if self._paths is None and self._index is None:
                    self._paths = paths
                    self._index = index
                    self._start_watcher_if_needed()
        self._paths, self._index, _search_tool, _get_tool = cached
        return cached

    def _current_owner_id(self) -> str | None:
        runtime_ctx = current_runtime_context()
        if runtime_ctx is None:
            return None
        return runtime_ctx.owner_id

    def _build_owner_aware_memory_search_tool(self) -> Any:
        @tool
        async def memory_search(query: str, limit: int = 5) -> str:
            """搜索长期记忆（MEMORY.md、memory/*.md、extra/*.md），返回相关片段。"""
            _paths, _index, search_tool, _get_tool = self._activate_owner(
                self._current_owner_id()
            )
            return await search_tool.ainvoke({"query": query, "limit": limit})

        return memory_search

    def _build_owner_aware_memory_get_tool(self) -> Any:
        @tool
        async def memory_get(path: str, from_line: int = 0, lines: int = 0) -> str:
            """读取长期记忆 Markdown。"""
            _paths, _index, _search_tool, get_tool = self._activate_owner(
                self._current_owner_id()
            )
            return await get_tool.ainvoke(
                {"path": path, "from_line": from_line, "lines": lines}
            )

        return memory_get

    async def _before_run(self, inp: BeforeRunInput) -> BeforeRunResult | None:
        """首轮运行前追加 Memory Recall 提示词。"""
        if not _memory_enabled():
            return None
        with start_span(
            "memory.before_run",
            attributes={"luckbot.owner_id": default_owner_id(inp.owner_id or "local")},
        ):
            self._activate_owner(inp.owner_id)
            return BeforeRunResult(
                system_prompt=inp.system_prompt + "\n\n" + MEMORY_RECALL,
            )

    async def _after_run(self, inp: AfterRunInput) -> None:
        """每轮 agent 结束后把磁盘 md 增量同步进索引（与 watcher 互补）。"""
        await self._sync_now()

    async def _before_llm(self, inp: BeforeLLMCallInput) -> BeforeLLMCallResult | None:
        """每次调 LLM 前尝试上下文压缩（见 agent.memory.compaction）。"""
        if not _memory_enabled():
            return None
        with start_span(
            "memory.compaction.check",
            attributes={"luckbot.owner_id": default_owner_id(inp.owner_id or "local")},
        ):
            self._activate_owner(inp.owner_id)
            if not self._paths:
                return None
            return await maybe_compact_before_llm(
                inp,
                self._paths,
                memory_index=self._index,
                embedding_provider=self._provider,
            )

    async def _sync_now(self) -> None:
        """同步长期记忆 Markdown。"""
        if not _memory_enabled() or not self._index:
            return
        with start_span("memory.sync"):
            try:
                await self._index.sync()
                increment_counter("luckbot_memory_sync_total")
            except Exception as exc:
                record_exception(exc)
                log_exception(logger, "memory.sync_failed", exc)


__all__ = ["MemoryPlugin", "archive_last_session_to_markdown"]
