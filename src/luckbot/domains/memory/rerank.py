"""检索后处理：可选阿里云百炼 text-rerank、可选 MMR 去冗余（需 numpy + embeddings）。"""

from __future__ import annotations

import json
import logging
import os
import struct
import urllib.error
import urllib.request
from typing import Literal

import numpy as np

from luckbot.core.config.env_parse import env_int

logger = logging.getLogger(__name__)

DASHSCOPE_RERANK_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)

StageMode = Literal["off", "auto", "force"]


def dashscope_api_key() -> str:
    """百炼 API Key，环境变量 ``DASHSCOPE_API_KEY``。"""
    return (os.getenv("DASHSCOPE_API_KEY") or "").strip()

DEFAULT_MIN_CANDIDATES = 8


def parse_memory_stage_mode(env_key: str) -> StageMode:
    """读取 ``off`` / ``auto`` / ``force``；未设置或非法值视为 ``off``。"""
    raw = (os.getenv(env_key) or "").strip().lower()
    if raw in ("off", "auto", "force"):
        return raw  # type: ignore[return-value]
    return "off"


def default_min_candidates(env_key: str, default: int = DEFAULT_MIN_CANDIDATES) -> int:
    return max(0, env_int(env_key, default))


def should_run_stage(
    tool_override: bool | None,
    mode: StageMode,
    pool_len: int,
    min_n: int,
    *,
    can_run: bool,
) -> bool:
    """是否执行 rerank/MMR 阶段：工具 False → 关；工具 True → 仅当 can_run；否则按 mode +池大小。"""
    if tool_override is False:
        return False
    if tool_override is True:
        return can_run
    if mode == "off":
        return False
    if mode == "force":
        return can_run
    return pool_len >= min_n and can_run


def fetch_dashscope_rerank_scores_sync(
    *,
    query: str,
    documents: list[str],
    api_key: str,
    model: str | None = None,
    timeout_s: float = 60.0,
) -> list[float] | None:
    """调用阿里云百炼 text-rerank；返回与 ``documents`` 等长的 relevance分数（按原始下标对齐）。"""
    if not documents:
        return []
    m = (model or "").strip() or (os.getenv("LUCKBOT_MEMORY_RERANK_MODEL") or "").strip()
    if not m:
        raise RuntimeError(
            "请设置 LUCKBOT_MEMORY_RERANK_MODEL（发起百炼 rerank 前须与 API Key 配套）。"
        )
    n = len(documents)
    body = json.dumps(
        {
            "model": m,
            "input": {"query": query, "documents": documents},
            "parameters": {
                "return_documents": False,
                "top_n": n,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        DASHSCOPE_RERANK_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("百炼 rerank 请求失败: %s", e)
        return None

    out = data.get("output") or {}
    results = out.get("results")
    if not isinstance(results, list) or not results:
        logger.warning("百炼 rerank 返回无 results: %s", data)
        return None

    scores = [0.0] * n
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= n:
            continue
        try:
            scores[idx] = float(item.get("relevance_score", 0.0))
        except (TypeError, ValueError):
            continue
    return scores


def blob_to_vec(blob: bytes) -> np.ndarray:
    """chunks.embedding BLOB（float32）→ numpy，供余弦/MMR。"""
    n = len(blob) // 4
    return np.asarray(struct.unpack(f"{n}f", blob), dtype=np.float64)


def mmr_select(
    ordered_ids: list[str],
    query_vec: list[float],
    id_to_blob: dict[str, bytes],
    *,
    top_k: int,
    lambda_mult: float = 0.5,
) -> list[str]:
    """在有序候选上选 top_k 个 chunk_id，最大化 MMR 目标。"""
    if not ordered_ids or top_k <= 0:
        return []
    q = np.asarray(query_vec, dtype=np.float64)
    nq = np.linalg.norm(q)
    if nq < 1e-12:
        return ordered_ids[:top_k]

    vecs: dict[str, np.ndarray] = {}
    sim_q: dict[str, float] = {}
    for cid in ordered_ids:
        blob = id_to_blob.get(cid)
        if not blob:
            continue
        try:
            v = blob_to_vec(blob)
        except struct.error:
            continue
        nv = np.linalg.norm(v)
        if nv < 1e-12:
            continue
        vecs[cid] = v
        sim_q[cid] = float(np.dot(q, v) / (nq * nv))

    candidates = [cid for cid in ordered_ids if cid in sim_q]
    if not candidates:
        return ordered_ids[:top_k]

    selected: list[str] = []
    remaining = set(candidates)
    first = max(candidates, key=lambda c: sim_q[c])
    selected.append(first)
    remaining.discard(first)

    while len(selected) < top_k and remaining:
        best_id: str | None = None
        best_score = -1e300
        for cid in remaining:
            v = vecs[cid]
            max_sim = 0.0
            for sid in selected:
                sv = vecs[sid]
                nv = np.linalg.norm(v)
                ns = np.linalg.norm(sv)
                if nv < 1e-12 or ns < 1e-12:
                    continue
                s = float(np.dot(v, sv) / (nv * ns))
                if s > max_sim:
                    max_sim = s
            mmr = lambda_mult * sim_q[cid] - (1.0 - lambda_mult) * max_sim
            if mmr > best_score:
                best_score = mmr
                best_id = cid
        if best_id is None:
            break
        selected.append(best_id)
        remaining.discard(best_id)

    return selected
