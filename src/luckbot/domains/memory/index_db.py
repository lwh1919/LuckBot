"""SQLite 记忆索引：增量同步长期记忆 Markdown。

表：``files`` 记录路径+内容 hash；``chunks`` 存分块文本与 embedding BLOB；
``chunks_fts`` FTS5 全文；``embedding_cache`` 按 text_hash+model 缓存向量；
可选 ``chunks_vec``（sqlite-vec）用于 KNN。``MemoryIndex.sync`` 按文件 hash 跳过未变更文件。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import struct
import threading
import time
from pathlib import Path
from typing import Any

from luckbot.core.config.env_parse import env_int

from .chunking import chunk_markdown
from .embeddings import EmbeddingProvider, build_embedding_provider
from .paths import (
    ensure_memory_tree,
    list_memory_documents,
)
from .sqlite_vec import knn_search, serialize_embedding, try_load_sqlite_vec
from .types import IndexSourceDocument, MemoryPaths

logger = logging.getLogger(__name__)


def _hash_file(content: str) -> str:
    """整文件内容 sha256，用于判断是否需要重新分块与 embedding。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _chunk_id(
    source: str,
    path: str,
    sl: int,
    el: int,
    chunk_hash: str,
    model: str,
    ordinal: int,
) -> str:
    """稳定主键：含块序号，避免同文件内元数据重复的块共用一个 id。"""
    raw = f"{source}:{path}:{sl}:{el}:{chunk_hash}:{model}:{ordinal}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _open_db(index_path: str) -> sqlite3.Connection:
    """WAL + busy_timeout，便于多读少写与并发同步。"""
    Path(index_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _init_fts(conn: sqlite3.Connection) -> None:
    """创建 ``chunks_fts``；优先 unicode61 分词，失败则降级。"""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
    )
    if cur.fetchone():
        return
    for tokenize in ("porter unicode61", "porter ascii", None):
        try:
            if tokenize:
                conn.execute(
                    f"""
                    CREATE VIRTUAL TABLE chunks_fts USING fts5(
                      text,
                      chunk_id UNINDEXED,
                      path UNINDEXED,
                      source UNINDEXED,
                      model UNINDEXED,
                      start_line UNINDEXED,
                      end_line UNINDEXED,
                      tokenize='{tokenize}'
                    )
                    """
                )
            else:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE chunks_fts USING fts5(
                      text,
                      chunk_id UNINDEXED,
                      path UNINDEXED,
                      source UNINDEXED,
                      model UNINDEXED,
                      start_line UNINDEXED,
                      end_line UNINDEXED
                    )
                    """
                )
            return
        except sqlite3.OperationalError as e:
            logger.warning("FTS5 建表尝试失败 (%s): %s", tokenize, e)
            try:
                conn.execute("DROP TABLE IF EXISTS chunks_fts")
            except sqlite3.OperationalError:
                pass
    raise RuntimeError("无法创建 chunks_fts")


def _init_schema(
    conn: sqlite3.Connection, *, embedding_dim: int, vec_enabled: bool
) -> None:
    """建表；若启用 vec 且维数变化会 DROP 旧 ``chunks_vec`` 再建。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
          path TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'memory',
          hash TEXT NOT NULL,
          mtime_ms INTEGER NOT NULL,
          size INTEGER NOT NULL,
          PRIMARY KEY (path, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
          id TEXT PRIMARY KEY,
          path TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'memory',
          start_line INTEGER NOT NULL,
          end_line INTEGER NOT NULL,
          chunk_hash TEXT NOT NULL,
          model TEXT NOT NULL,
          text TEXT NOT NULL,
          embedding BLOB,
          vec_rowid INTEGER,
          updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embedding_cache (
          text_hash TEXT NOT NULL,
          model TEXT NOT NULL,
          embedding BLOB NOT NULL,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY (text_hash, model)
        )
        """
    )
    _init_fts(conn)
    if vec_enabled:
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
        )
        row = cur.fetchone()
        want = f"float[{embedding_dim}]"
        if row and row[0] and want not in (row[0] or ""):
            conn.execute("DROP TABLE IF EXISTS chunks_vec")
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
              embedding float[{embedding_dim}]
            )
            """
        )
    conn.commit()


class MemoryIndex:
    """打开 ``paths.index_sqlite``，按需加载 sqlite-vec，提供 ``sync`` 与向量 KNN。"""

    def __init__(
        self,
        paths: MemoryPaths,
        *,
        provider: EmbeddingProvider | None = None,
        embedding_dim: int | None = None,
    ) -> None:
        self.paths = paths
        self._conn = _open_db(paths.index_sqlite)
        self._db_lock = threading.RLock()
        self.provider = provider or build_embedding_provider()
        self.embedding_dim = embedding_dim or env_int(
            "LUCKBOT_MEMORY_EMBEDDING_DIM", 1536
        )
        self.vec_enabled = try_load_sqlite_vec(self._conn)
        _init_schema(
            self._conn,
            embedding_dim=self.embedding_dim,
            vec_enabled=self.vec_enabled,
        )
        if not self.vec_enabled:
            try:
                self._conn.execute("DROP TABLE IF EXISTS chunks_vec")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def _load_cached_embeddings(
        self, pairs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], bytes]:
        """(text_hash, model) -> blob"""
        if not pairs:
            return {}
        out: dict[tuple[str, str], bytes] = {}
        for th, model in pairs:
            r = self._conn.execute(
                "SELECT embedding FROM embedding_cache WHERE text_hash=? AND model=?",
                (th, model),
            ).fetchone()
            if r:
                out[(th, model)] = r[0]
        return out

    def _save_cache(self, text_hash: str, model: str, blob: bytes) -> None:
        now = int(time.time() * 1000)
        self._conn.execute(
            """
            INSERT INTO embedding_cache (text_hash, model, embedding, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(text_hash, model) DO UPDATE SET
              embedding=excluded.embedding, updated_at=excluded.updated_at
            """,
            (text_hash, model, blob, now),
        )

    def _delete_path_chunks(self, rel_path: str, source: str = "memory") -> None:
        """文件变更或清空时：删 chunks / fts / vec 中该 path 下所有块。"""
        rows = self._conn.execute(
            "SELECT id, vec_rowid FROM chunks WHERE path=? AND source=?",
            (rel_path, source),
        ).fetchall()
        for cid, vr in rows:
            try:
                self._conn.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (cid,))
            except sqlite3.OperationalError:
                pass
            if vr is not None and self.vec_enabled:
                try:
                    self._conn.execute(
                        "DELETE FROM chunks_vec WHERE rowid=?", (int(vr),)
                    )
                except sqlite3.OperationalError:
                    pass
        self._conn.execute(
            "DELETE FROM chunks WHERE path=? AND source=?", (rel_path, source)
        )

    def _next_vec_rowid(self) -> int:
        """sqlite-vec 表 rowid 需显式插入时自增。"""
        if not self.vec_enabled:
            return 0
        r = self._conn.execute(
            "SELECT COALESCE(MAX(rowid), 0) + 1 FROM chunks_vec"
        ).fetchone()
        return int(r[0]) if r else 1

    def _delete_removed_paths(self, source: str, current_paths: set[str]) -> None:
        rows = self._conn.execute(
            "SELECT path FROM files WHERE source=?",
            (source,),
        ).fetchall()
        for (path,) in rows:
            rel_path = str(path)
            if rel_path in current_paths:
                continue
            self._delete_path_chunks(rel_path, source)
            self._conn.execute(
                "DELETE FROM files WHERE path=? AND source=?",
                (rel_path, source),
            )

    async def sync(self) -> None:
        """同步长期记忆 Markdown 到统一索引。"""
        ensure_memory_tree(self.paths)
        model = self.provider.model
        documents: list[IndexSourceDocument] = list_memory_documents(self.paths)
        grouped_paths: dict[str, set[str]] = {"memory": set(), "sessions": set()}
        for doc in documents:
            grouped_paths.setdefault(doc.source, set()).add(doc.path)

        with self._db_lock:
            for source, current_paths in grouped_paths.items():
                self._delete_removed_paths(source, current_paths)

        for doc in documents:
            with self._db_lock:
                row = self._conn.execute(
                    "SELECT hash FROM files WHERE path=? AND source=?",
                    (doc.path, doc.source),
                ).fetchone()
            if row and row[0] == doc.content_hash:
                continue
            await self._reindex_file(
                doc.path,
                doc.content,
                doc.content_hash,
                doc.mtime_ms,
                doc.size,
                model,
                source=doc.source,
            )
        with self._db_lock:
            self._conn.commit()

    def sync_blocking(self) -> None:
        """非 async 上下文包装；注意已在运行中的 event loop 里勿用。"""
        import asyncio

        asyncio.get_event_loop().run_until_complete(self.sync())

    async def _reindex_file(
        self,
        rel_path: str,
        content: str,
        file_hash: str,
        mtime_ms: int,
        size: int,
        model: str,
        *,
        source: str = "memory",
    ) -> None:
        """分块 → 批 embedding（走缓存）→ 写 chunks / fts / 可选 vec → 更新 files。

        ``await embed`` 在锁外执行，避免阻塞事件循环；SQLite 写路径用 ``_db_lock``
        与 watchdog 线程里的 ``sync`` 互斥，防止并发写导致 ``UNIQUE`` 冲突。
        """
        with self._db_lock:
            self._delete_path_chunks(rel_path, source)
            chunks = chunk_markdown(content)
            if not chunks:
                self._conn.execute(
                    """
                    INSERT INTO files (path, source, hash, mtime_ms, size)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(path, source) DO UPDATE SET
                      hash=excluded.hash, mtime_ms=excluded.mtime_ms, size=excluded.size
                    """,
                    (rel_path, source, file_hash, mtime_ms, size),
                )
                return

            cache_keys: list[tuple[str, str]] = [
                (ch.chunk_hash, model) for ch in chunks
            ]
            cached = self._load_cached_embeddings(cache_keys)
            vectors: list[list[float]] = [[] for _ in chunks]
            need_idx: list[int] = []
            for i, ch in enumerate(chunks):
                key = (ch.chunk_hash, model)
                if key in cached:
                    blob = cached[key]
                    vectors[i] = list(struct.unpack(f"{len(blob)//4}f", blob))
                else:
                    need_idx.append(i)

        if need_idx:
            texts = [chunks[i].text for i in need_idx]
            new_vecs = await self.provider.embed_documents(texts)
            if len(new_vecs) != len(need_idx):
                raise RuntimeError("embedding 批大小异常")
        else:
            new_vecs = []

        with self._db_lock:
            for j, i in enumerate(need_idx):
                vec = new_vecs[j]
                vectors[i] = vec
                blob = serialize_embedding(vec)
                self._save_cache(chunks[i].chunk_hash, model, blob)

            now = int(time.time() * 1000)
            for ordinal, (ch, vec) in enumerate(zip(chunks, vectors)):
                if len(vec) != self.embedding_dim:
                    logger.warning(
                        "向量维 %s 与配置 %s 不一致，将按实际维写库（可能影响 vec）",
                        len(vec),
                        self.embedding_dim,
                    )
                cid = _chunk_id(
                    source,
                    rel_path,
                    ch.start_line,
                    ch.end_line,
                    ch.chunk_hash,
                    model,
                    ordinal,
                )
                blob = serialize_embedding(vec)
                vec_rowid: int | None = None
                if self.vec_enabled:
                    vec_rowid = self._next_vec_rowid()
                    blob_vec = serialize_embedding(vec)
                    try:
                        self._conn.execute(
                            "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
                            (vec_rowid, blob_vec),
                        )
                    except sqlite3.OperationalError as e:
                        logger.warning("写入 chunks_vec 失败，本 chunk 仅存 BLOB: %s", e)
                        vec_rowid = None

                self._conn.execute(
                    """
                    INSERT INTO chunks (
                      id, path, source, start_line, end_line, chunk_hash, model,
                      text, embedding, vec_rowid, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid,
                        rel_path,
                        source,
                        ch.start_line,
                        ch.end_line,
                        ch.chunk_hash,
                        model,
                        ch.text,
                        blob,
                        vec_rowid,
                        now,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO chunks_fts(
                      text, chunk_id, path, source, model, start_line, end_line
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ch.text,
                        cid,
                        rel_path,
                        source,
                        model,
                        ch.start_line,
                        ch.end_line,
                    ),
                )

            self._conn.execute(
                """
                INSERT INTO files (path, source, hash, mtime_ms, size)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path, source) DO UPDATE SET
                  hash=excluded.hash, mtime_ms=excluded.mtime_ms, size=excluded.size
                """,
                (rel_path, source, file_hash, mtime_ms, size),
            )

    def vec_knn(self, query_vec: list[float], k: int) -> list[tuple[int, float]]:
        """包装 ``sqlite_vec.knn_search``；未启用扩展时返回空列表。"""
        if not self.vec_enabled:
            return []
        return knn_search(self._conn, query_vec=query_vec, k=k, dim=len(query_vec))
