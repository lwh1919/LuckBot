"""LuckBot 本地记忆：目录布局、分块、SQLite 索引、向量/全文混合检索。

典型流程：``resolve_memory_paths`` → ``MemoryIndex.sync`` 建/更新索引 → ``hybrid_search`` 查询。
当前索引和读取工具仅包含长期记忆 Markdown；会话原始记录仍保留在 sessions/*.jsonl 中。
"""

from __future__ import annotations

from .embeddings import (
    DashScopeEmbeddingProvider,
    EmbeddingProvider,
    build_embedding_provider,
)
from .index_db import MemoryIndex
from .paths import (
    clear_memory_store,
    ensure_memory_tree,
    list_memory_documents,
    resolve_memory_paths,
    resolve_memory_read_path,
)
from .search import hybrid_search, hits_to_json_payload
from .types import Chunk, Hit, IndexSourceDocument, MemoryPaths, SearchOptions

__all__ = [
    "Chunk",
    "DashScopeEmbeddingProvider",
    "EmbeddingProvider",
    "Hit",
    "IndexSourceDocument",
    "hits_to_json_payload",
    "hybrid_search",
    "MemoryIndex",
    "MemoryPaths",
    "SearchOptions",
    "build_embedding_provider",
    "clear_memory_store",
    "ensure_memory_tree",
    "list_memory_documents",
    "resolve_memory_paths",
    "resolve_memory_read_path",
]
