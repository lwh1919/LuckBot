"""Memory Flush 子循环重入标记：嵌套时 ``maybe_compact_before_llm`` 直接跳过。"""

from __future__ import annotations

from contextvars import ContextVar

memory_flush_nested: ContextVar[bool] = ContextVar(
    "memory_flush_nested", default=False
)
