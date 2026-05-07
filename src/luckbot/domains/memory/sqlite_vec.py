"""可选扩展 sqlite-vec：加载后用 ``chunks_vec`` 虚拟表做 KNN；未加载则检索回退 Python 余弦。

环境：``LUCKBOT_MEMORY_USE_SQLITE_VEC`` 非 off 时尝试 ``import sqlite_vec`` 并 ``load(conn)``。
"""

from __future__ import annotations

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


def try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """扩展加载成功返回 True；失败记 warning，后续用 chunks 表内 BLOB + Python 算相似度。"""
    use = (os.getenv("LUCKBOT_MEMORY_USE_SQLITE_VEC", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if not use:
        return False
    try:
        import sqlite_vec  # type: ignore

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as e:
        logger.warning("sqlite-vec 未加载，向量检索将回退 Python：%s", e)
        return False


def serialize_embedding(vec: list[float]) -> bytes:
    """优先 sqlite_vec.serialize_float32；否则 struct pack 为 float32 小端。"""
    try:
        import sqlite_vec  # type: ignore

        return sqlite_vec.serialize_float32(vec)
    except Exception:
        import struct

        return struct.pack(f"{len(vec)}f", *vec)


def knn_search(
    conn: sqlite3.Connection,
    *,
    query_vec: list[float],
    k: int,
    dim: int,
) -> list[tuple[int, float]]:
    """``chunks_vec`` MATCH JSON 查询向量；返回 ``(rowid, distance)``，distance 越小越近。"""
    import json

    try:
        q = json.dumps(query_vec)
        cur = conn.execute(
            f"""
            SELECT rowid, distance FROM chunks_vec
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
            """,
            (q, k),
        )
        return [(int(r[0]), float(r[1])) for r in cur.fetchall()]
    except Exception:
        return []
