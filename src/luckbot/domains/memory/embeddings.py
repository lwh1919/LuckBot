"""文本 → 向量：仅阿里云百炼 DashScope（``dashscope.TextEmbedding``）。

环境变量 ``DASHSCOPE_API_KEY`` 与 ``LUCKBOT_MEMORY_EMBEDDING_MODEL``（须同时非空，无内置默认模型名）。
``LUCKBOT_MEMORY_EMBEDDING_DIM`` 可截断前 N 维再 L2 归一化，须与 MemoryIndex / sqlite-vec 维数一致。

``build_embedding_provider()`` → ``DashScopeEmbeddingProvider``；``MemoryPlugin`` / ``MemoryIndex`` 依赖 ``EmbeddingProvider``。
"""

from __future__ import annotations

import asyncio
import os
from typing import Protocol, runtime_checkable

import numpy as np

from luckbot.core.config.env_parse import env_int


@runtime_checkable
class EmbeddingProvider(Protocol):
    """记忆模块与检索共用的向量接口。"""

    model: str
    backend_name: str

    async def embed_query(self, text: str) -> list[float]: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize_rows(rows: np.ndarray) -> list[list[float]]:
    """按行 L2 归一化；检索侧用余弦相似度时与「单位向量点积」等价。"""
    if rows.size == 0:
        return []
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    out = (rows / norms).astype(np.float32)
    return [row.tolist() for row in out]


def _dashscope_api_key_from_env() -> str:
    return (os.getenv("DASHSCOPE_API_KEY") or "").strip()


def _embedding_dim_cap_from_env() -> int | None:
    raw = (os.getenv("LUCKBOT_MEMORY_EMBEDDING_DIM", "") or "").strip()
    if not raw:
        return None
    v = env_int("LUCKBOT_MEMORY_EMBEDDING_DIM", 0)
    return v if v > 0 else None

# 非对称检索
class DashScopeEmbeddingProvider:
    """阿里云百炼 text-embedding；文档 ``text_type=document``，查询 ``text_type=query``。"""

    backend_name = "dashscope"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dim_cap: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        try:
            import dashscope  # noqa: F401
        except ImportError as e:
            raise RuntimeError("百炼向量需安装：pip install dashscope") from e

        key = (api_key or _dashscope_api_key_from_env()).strip()
        if not key:
            raise RuntimeError("请设置 DASHSCOPE_API_KEY")

        self._api_key = key
        resolved = (model or (os.getenv("LUCKBOT_MEMORY_EMBEDDING_MODEL") or "").strip())
        if not resolved:
            raise RuntimeError("请设置 LUCKBOT_MEMORY_EMBEDDING_MODEL（须与 DASHSCOPE_API_KEY 配套）。")
        self.model = resolved
        self._dim_cap = dim_cap if dim_cap is not None else _embedding_dim_cap_from_env()
        self._batch = max(
            1,
            batch_size or env_int("LUCKBOT_MEMORY_DASHSCOPE_EMBED_BATCH", 10),
        )

    def _post_process(self, rows: list[list[float]]) -> list[list[float]]:
        if not rows:
            return []
        arr = np.asarray(rows, dtype=np.float64)
        if self._dim_cap is not None:
            end = min(arr.shape[1], self._dim_cap)
            arr = arr[:, :end]
        return _l2_normalize_rows(arr)

    def _embed_batch_sync(self, texts: list[str], *, text_type: str) -> list[list[float]]:
        from http import HTTPStatus

        import dashscope

        rsp = dashscope.TextEmbedding.call(
            model=self.model,
            input=texts,
            text_type=text_type,
            api_key=self._api_key,
        )
        if rsp.status_code != HTTPStatus.OK:
            msg = getattr(rsp, "message", None) or str(rsp)
            raise RuntimeError(f"DashScope 向量 API 错误: {msg}")

        output = getattr(rsp, "output", None)
        if not isinstance(output, dict):
            raise RuntimeError("DashScope 向量返回缺少 output")
        embs = output.get("embeddings")
        if not isinstance(embs, list) or not embs:
            raise RuntimeError("DashScope 向量返回无 embeddings")

        def _ord_key(item: object) -> int:
            if isinstance(item, dict):
                return int(item.get("text_index", item.get("index", 0)))
            return int(getattr(item, "text_index", getattr(item, "index", 0)))

        ordered = sorted(embs, key=_ord_key)
        raw_vecs: list[list[float]] = []
        for item in ordered:
            if isinstance(item, dict):
                emb = item.get("embedding")
            else:
                emb = getattr(item, "embedding", None)
            if emb is None:
                raise RuntimeError("embedding 项缺少 embedding 字段")
            raw_vecs.append([float(x) for x in emb])
        if len(raw_vecs) != len(texts):
            raise RuntimeError("embedding 条数与输入不一致")
        return raw_vecs

    def _embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            batch = texts[i : i + self._batch]
            raw = self._embed_batch_sync(batch, text_type="document")
            all_vecs.extend(self._post_process(raw))
        return all_vecs

    def _embed_query_sync(self, text: str) -> list[float]:
        raw = self._embed_batch_sync([text], text_type="query")
        vecs = self._post_process(raw)
        return vecs[0] if vecs else []

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_query_sync, text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_documents_sync, texts)


def build_embedding_provider() -> EmbeddingProvider:
    """供 ``MemoryIndex`` / ``MemoryPlugin`` 使用；须配置 ``DASHSCOPE_API_KEY`` 与 ``LUCKBOT_MEMORY_EMBEDDING_MODEL``。"""
    return DashScopeEmbeddingProvider()
