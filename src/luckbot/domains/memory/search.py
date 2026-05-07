"""FTS5（BM25）与向量相似度混合打分，可选 rerank HTTP 与 MMR。

流程概要：关键词检索 → 查询 embedding → sqlite-vec KNN 或全表/预筛选余弦 → 加权融合 → 截断池 → 可选 rerank/MMR → 返回结构化 results。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import struct
from typing import Any

from luckbot.core.config.env_parse import env_float, env_int

from .embeddings import EmbeddingProvider
from .index_db import MemoryIndex
from .rerank import (
    dashscope_api_key,
    default_min_candidates,
    fetch_dashscope_rerank_scores_sync,
    mmr_select,
    parse_memory_stage_mode,
    should_run_stage,
)
from .types import Hit, SearchOptions

logger = logging.getLogger(__name__)


def _search_result_chunk_text(raw: str) -> str:
    """memory_search 每条结果中的正文：默认返回索引内整段 chunk（与分块上限一致）。

    ``LUCKBOT_MEMORY_SEARCH_SNIPPET_MAX_CHARS`` > 0 时截断并加省略号，用于控制工具返回体积。
    """
    cap = env_int("LUCKBOT_MEMORY_SEARCH_SNIPPET_MAX_CHARS", 0)
    if cap <= 0 or len(raw) <= cap:
        return raw
    return raw[:cap] + "…"


def _rerank_document_text(raw: str) -> str:
    """传给外部 rerank 的单条文档；默认8000 字符上限，避免过长正文拖垮 API。"""
    cap = env_int("LUCKBOT_MEMORY_RERANK_DOC_MAX_CHARS", 8000)
    if cap <= 0 or len(raw) <= cap:
        return raw
    return raw[:cap] + "…"


def _escape_fts_query(q: str) -> str:
    """空白分词后用 OR 连接，避开 FTS 特殊字符；空查询返回空串。"""
    s = q.strip()
    if not s:
        return ""
    s = re.sub(r'["*]', " ", s)
    parts = [p for p in re.split(r"\s+", s) if p]
    if not parts:
        return ""
    return " OR ".join(parts)


def _min_max_norm(scores: dict[str, float]) -> dict[str, float]:
    """将 chunk_id → 分数 压到 [0,1]，便于与另一路分值加权。"""
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def _cosine(a: list[float], b: list[float]) -> float:
    """回退路线的向量相似度（与 vec 距离尺度无关，后续会 min-max）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


def _blob_to_vec(blob: bytes) -> list[float]:
    """chunks 表中 embedding BLOB → float 列表。"""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


async def hybrid_search(
    index: MemoryIndex,
    provider: EmbeddingProvider,
    query: str,
    opts: SearchOptions | None = None,
) -> dict[str, Any]:
    """返回 ``results``（path/score/snippet/source 等）及 ``backend/model/rerank/mmr`` 元信息。"""
    opts = opts or SearchOptions()
    conn: sqlite3.Connection = index.conn
    fallback = False

    # --- keyword ---
    text_scores: dict[str, float] = {}
    fts_q = _escape_fts_query(query)
    if fts_q:
        try:
            cur = conn.execute(
                """
                SELECT chunk_id, bm25(chunks_fts) AS r
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
                ORDER BY r
                LIMIT 200
                """,
                (fts_q,),
            )
            for cid, r in cur.fetchall():
                cid = str(cid)
                br = float(r) if r is not None else 0.0
                text_scores[cid] = 1.0 / (1.0 + max(0.0, br))
        except sqlite3.OperationalError as e:
            logger.warning("FTS 查询失败: %s", e)

    # --- vector ---
    vector_scores: dict[str, float] = {}
    qvec: list[float] = []
    try:
        qvec = await provider.embed_query(query)
    except Exception as e:
        logger.warning("query embedding 失败: %s", e)
        fallback = True

    meta: dict[str, dict[str, Any]] = {}
    cur2 = conn.execute(
        """
        SELECT id, path, source, start_line, end_line, text, embedding, vec_rowid
        FROM chunks
        """
    )
    rows = cur2.fetchall()
    for cid, path, source, sl, el, text, emb_blob, _vr in rows:
        meta[str(cid)] = {
            "path": path,
            "source": source,
            "start_line": sl,
            "end_line": el,
            "text": text or "",
            "embedding": emb_blob,
        }

    if qvec and index.vec_enabled:
        k = max(opts.vec_knn_k, opts.max_results * 10, opts.prefilter_m)
        knn = index.vec_knn(qvec, k=k)
        if knn:
            dists = [d for _, d in knn]
            dlo, dhi = min(dists), max(dists)
            for rowid, dist in knn:
                row = conn.execute(
                    "SELECT id FROM chunks WHERE vec_rowid=?",
                    (int(rowid),),
                ).fetchone()
                if not row:
                    continue
                cid = str(row[0])
                if dhi - dlo < 1e-9:
                    vector_scores[cid] = 1.0
                else:
                    vector_scores[cid] = 1.0 - (float(dist) - dlo) / (dhi - dlo)

    if qvec and (not vector_scores or not index.vec_enabled):
        cands = list(meta.keys())
        if opts.prefilter_m > 0 and text_scores:
            top_txt = sorted(text_scores.keys(), key=lambda x: text_scores[x], reverse=True)[
                : opts.prefilter_m
            ]
            cands = top_txt
        sims: dict[str, float] = {}
        for cid in cands:
            blob = meta[cid].get("embedding")
            if not blob:
                continue
            try:
                vec = _blob_to_vec(blob)
            except struct.error:
                continue
            sims[cid] = _cosine(qvec, vec)
        vector_scores = _min_max_norm(sims)

    vector_scores = _min_max_norm(vector_scores) if vector_scores else {}
    text_scores = _min_max_norm(text_scores) if text_scores else {}

    merged: dict[str, float] = {}
    if vector_scores and not text_scores:
        merged = dict(vector_scores)
    elif text_scores and not vector_scores:
        merged = dict(text_scores)
    else:
        ids = set(vector_scores) | set(text_scores)
        for cid in ids:
            vs = vector_scores.get(cid, 0.0)
            ts = text_scores.get(cid, 0.0)
            merged[cid] = opts.vector_weight * vs + opts.text_weight * ts

    ranked_pairs = [(cid, s) for cid, s in merged.items() if s >= opts.min_score]
    ranked_pairs.sort(key=lambda x: -x[1])
    pool_n = max(opts.max_results * 4, 16)
    pool_pairs = ranked_pairs[:pool_n]
    if not pool_pairs:
        return {
            "results": [],
            "backend": provider.backend_name,
            "provider": provider.backend_name,
            "model": provider.model,
            "fallback": fallback,
            "rerank": False,
            "mmr": False,
        }

    def _hit_for(cid: str, score: float) -> Hit:
        m = meta[cid]
        tx = m["text"]
        shown = _search_result_chunk_text(tx)
        return Hit(
            chunk_id=cid,
            path=str(m["path"]),
            source=str(m["source"]),
            start_line=int(m["start_line"]),
            end_line=int(m["end_line"]),
            text=tx,
            score=score,
            snippet=shown,
        )

    id_order = [cid for cid, _ in pool_pairs]
    scores_map = dict(pool_pairs)
    used_rerank = False
    used_mmr = False
    pool_len = len(id_order)

    ds_key = dashscope_api_key()
    rerank_key = (os.getenv("LUCKBOT_MEMORY_RERANK_API_KEY") or "").strip() or ds_key
    can_rerank = bool(rerank_key)
    rerank_mode = parse_memory_stage_mode("LUCKBOT_MEMORY_RERANK_MODE")
    rerank_min_n = default_min_candidates("LUCKBOT_MEMORY_RERANK_MIN_CANDIDATES")
    do_rerank = should_run_stage(
        opts.rerank,
        rerank_mode,
        pool_len,
        rerank_min_n,
        can_run=can_rerank,
    )
    if do_rerank:
        texts = []
        for cid in id_order:
            m = meta[cid]
            tx = m["text"]
            texts.append(_rerank_document_text(tx))
        rs = (
            fetch_dashscope_rerank_scores_sync(
                query=query, documents=texts, api_key=rerank_key
            )
            if rerank_key
            else None
        )
        if rs is not None:
            used_rerank = True
            for i, cid in enumerate(id_order):
                scores_map[cid] = rs[i]
            id_order.sort(key=lambda c: -scores_map.get(c, 0.0))

    mmr_mode = parse_memory_stage_mode("LUCKBOT_MEMORY_MMR_MODE")
    mmr_min_n = default_min_candidates("LUCKBOT_MEMORY_MMR_MIN_CANDIDATES")
    can_mmr = bool(qvec)
    do_mmr = should_run_stage(
        opts.mmr,
        mmr_mode,
        pool_len,
        mmr_min_n,
        can_run=can_mmr,
    )

    if do_mmr and qvec:
        lambda_m = env_float("LUCKBOT_MEMORY_MMR_LAMBDA", 0.5)
        blobs = {cid: meta[cid]["embedding"] for cid in id_order if meta[cid].get("embedding")}
        if blobs:
            picked = mmr_select(
                id_order,
                qvec,
                blobs,
                top_k=opts.max_results,
                lambda_mult=lambda_m,
            )
            used_mmr = True
            hits = [_hit_for(cid, scores_map[cid]) for cid in picked]
        else:
            hits = [
                _hit_for(cid, scores_map[cid])
                for cid in id_order[: opts.max_results]
            ]
    else:
        hits = [
            _hit_for(cid, scores_map[cid]) for cid in id_order[: opts.max_results]
        ]

    return {
        "results": [
            {
                "path": h.path,
                "startLine": h.start_line,
                "endLine": h.end_line,
                "score": round(h.score, 4),
                "snippet": h.snippet,
                "source": h.source,
            }
            for h in hits
        ],
        "backend": provider.backend_name,
        "provider": provider.backend_name,
        "model": provider.model,
        "fallback": fallback,
        "rerank": used_rerank,
        "mmr": used_mmr,
    }


def hits_to_json_payload(payload: dict[str, Any]) -> str:
    """``hybrid_search`` 结果 dict → UTF-8 JSON 字符串（工具/插件直接返回）。"""
    return json.dumps(payload, ensure_ascii=False)
