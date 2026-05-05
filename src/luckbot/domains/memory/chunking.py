"""Markdown 分块：按「约 token×4」字符窗口切分，遇标题优先断块，块间可 overlap。

输出供 ``MemoryIndex`` 做 FTS5 与向量嵌入；``chunk_hash`` 用于缓存与稳定 chunk id。
当前这套切块逻辑服务于长期记忆 Markdown。
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import List, Tuple

from luckbot.core.config.env_parse import env_int

from .types import Chunk


def _tokens_to_chars_approx(tokens: int) -> int:
    """将「目标 token 数」换成近似字符上限（按 1 token≈4 字）；下限 32 避免块过小。"""
    return max(32, tokens * 4)


def _hash_text(s: str) -> str:
    """块正文 SHA-256，写入 ``Chunk.chunk_hash``；与 embedding 模型名等一并参与稳定主键。"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def chunk_markdown(
    content: str,
    *,
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> List[Chunk]:
    """将整篇 Markdown 切成带行号范围的 Chunk 列表（供 FTS + embedding）。

    策略概要：顺序扫行；遇到 ``#`` 标题行先「封口」当前块（语义边界）；累积字符超过
    ``max_chars`` 也会封口；封口时从块尾按 ``overlap_chars`` 回卷若干行进入下一块，
    减少边界两侧检索丢失上下文。
    """
    max_t = (
        max_tokens
        if max_tokens is not None
        else env_int("LUCKBOT_MEMORY_CHUNK_TOKENS", 400)
    )
    ov_t = (
        overlap_tokens
        if overlap_tokens is not None
        else env_int("LUCKBOT_MEMORY_CHUNK_OVERLAP", 80)
    )

    max_chars = _tokens_to_chars_approx(max_t)
    overlap_chars = max(0, _tokens_to_chars_approx(ov_t))

    lines = content.split("\n")
    heading_re = re.compile(r"^\s*#{1,6}\s+\S")
    chunks: List[Chunk] = []
    current_lines: List[Tuple[str, int]] = []
    current_size = 0

    def flush() -> None:
        """当前缓冲合成一个 Chunk；若有 overlap，从尾部保留若干行作为下一块起点。"""
        nonlocal current_lines, current_size
        if not current_lines:
            return
        text = "\n".join(t[0] for t in current_lines)
        chunks.append(
            Chunk(
                text=text,
                start_line=current_lines[0][1],
                end_line=current_lines[-1][1],
                chunk_hash=_hash_text(text),
            )
        )
        if overlap_chars > 0:
            ov: List[Tuple[str, int]] = []
            oc = 0
            for tl, ln in reversed(current_lines):
                ov.append((tl, ln))
                oc += len(tl) + 1  # 含换行
                if oc >= overlap_chars:
                    break
            ov.reverse()
            current_lines = ov
            current_size = sum(len(t[0]) + 1 for t in current_lines) - (
                1 if current_lines else 0
            )
        else:
            current_lines = []
            current_size = 0

    for line_no, line in enumerate(lines, start=1):
        line_len = len(line) + 1  # 换行符计入窗口
        # 新标题：优先在之前一行处断块，避免把两个章节挤在同一块里
        if current_lines and heading_re.match(line):
            flush()
        # 再塞入本行会超窗：先 flush 再追加
        if current_lines and current_size + line_len > max_chars:
            flush()
        current_lines.append((line, line_no))
        current_size += line_len

    flush()
    return chunks
