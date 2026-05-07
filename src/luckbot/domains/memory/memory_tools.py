"""memory_search / memory_get 工具工厂，供 MemoryPlugin 与 MemoryFlushToolsPlugin 共用。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain_core.tools import tool

from luckbot.core.config.env_parse import env_float, env_int
from .embeddings import EmbeddingProvider
from .index_db import MemoryIndex
from .paths import resolve_memory_read_path
from .search import hits_to_json_payload, hybrid_search
from .types import MemoryPaths, SearchOptions

logger = logging.getLogger(__name__)


def search_options_from_env() -> SearchOptions:
    """从环境变量组装 hybrid_search 参数。"""
    return SearchOptions(
        max_results=env_int("LUCKBOT_MEMORY_MAX_RESULTS", 6),
        min_score=env_float("LUCKBOT_MEMORY_MIN_SCORE", 0.35),
        vector_weight=env_float("LUCKBOT_MEMORY_VECTOR_WEIGHT", 0.7),
        text_weight=env_float("LUCKBOT_MEMORY_TEXT_WEIGHT", 0.3),
        prefilter_m=env_int("LUCKBOT_MEMORY_PREFILTER_M", 256),
        vec_knn_k=env_int("LUCKBOT_MEMORY_VEC_KNN_K", 64),
    )


def build_memory_search_tool(
    index: MemoryIndex,
    provider: EmbeddingProvider,
) -> Any:
    """构建异步 memory_search 工具（闭包捕获 index / provider）。"""

    @tool
    async def memory_search(
        query: str,
        max_results: int = 6,
        min_score: float = 0.35,
        rerank: bool | None = None,
        mmr: bool | None = None,
    ) -> str:
        """在长期记忆中做混合检索（语义+关键词）。返回 JSON；
        每条结果的 snippet 默认为索引内整段 chunk（可用 LUCKBOT_MEMORY_SEARCH_SNIPPET_MAX_CHARS 截断），
        设计上应优先直接利用 snippet 回答；只有需要回看记忆原文上下文时再调用 memory_get。
        rerank/mmr 可单次覆盖环境变量。"""
        opts = search_options_from_env()
        opts.max_results = max(1, min(max_results, 50))
        opts.min_score = min_score
        opts.rerank = rerank
        opts.mmr = mmr
        try:
            payload = await hybrid_search(index, provider, query, opts)
            return hits_to_json_payload(payload)
        except Exception as e:
            logger.warning("memory_search 失败: %s", e)
            return json.dumps({"results": [], "error": str(e)})

    return memory_search


def build_memory_get_tool(paths: MemoryPaths) -> Any:
    """构建异步 memory_get 工具。"""

    @tool
    async def memory_get(path: str, from_line: int = 0, lines: int = 0) -> str:
        """安全读取长期记忆 Markdown。适用于回看上下文、长文阅读或精确措辞确认；
        支持 MEMORY.md、memory/*.md、extra/*.md。
        from_line/lines 为 1-based 行号区间。"""
        rel = path.strip().replace("\\", "/")
        p = resolve_memory_read_path(rel, paths)
        if p is None:
            return json.dumps({"error": "path 不允许", "path": path})
        if not p.is_file():
            return json.dumps({"error": "文件不存在", "path": path})
        try:
            all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return json.dumps({"error": str(e), "path": path})
        total = len(all_lines)
        cap = env_int("LUCKBOT_MEMORY_GET_MAX_LINES", 500)
        if from_line <= 0:
            start, end = 0, total
        else:
            start = max(0, from_line - 1)
            end = total if lines <= 0 else min(total, start + lines)
        chunk = all_lines[start:end]
        if end - start > cap:
            chunk = chunk[:cap]
        text = "\n".join(chunk)
        return json.dumps({"text": text, "path": rel}, ensure_ascii=False)

    return memory_get


# 与 compaction / flush 共用的静默标记（模型仅输出此串表示无可写记忆）
NO_MEMORIES_TOKEN = "__NO_MEMORIES__"

MAX_MEMORY_WRITE_CHARS = int(
    (os.getenv("LUCKBOT_MEMORY_FLUSH_MAX_WRITE_CHARS", "") or "524288").strip() or "524288"
)
