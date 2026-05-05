"""调用 LLM 前上下文压缩：超 token 预算时先 Memory Flush（agent 子循环），再摘要 + 保留最近 K 条。

由 ``MemoryPlugin`` 注册为 ``before_llm_call``；仅影响当轮传给模型的 ``messages``，
不落盘 JSONL（会话持久化仍由 SessionPlugin 负责）。

Memory Flush 仅为带 ``memory_*`` 工具的短 ReAct 子循环（``agent.memory.flush_agent``）。
若缺少索引或嵌入提供方则跳过 flush 并打日志，不再使用其它回退写盘路径。

依赖环境变量：

- ``LUCKBOT_CONTEXT_TOKEN_BUDGET``：估算 token 超过此值才触发（未设置则整段逻辑不运行）。
- ``LUCKBOT_CONTEXT_KEEP_RECENT``：压缩后保留列表尾部 K 条（默认 10）。
- ``LUCKBOT_MEMORY_FLUSH``：若为 0/false/no/off 则跳过 flush 步骤。
- ``LUCKBOT_MEMORY_FLUSH_MAX_STEPS``：flush agent ReAct 最大步数（默认 12）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from luckbot.core.config.env_parse import env_int
from luckbot.core.llm.client import build_llm
from luckbot.core.observability import add_event, record_exception, start_langsmith_run, start_span
from luckbot.core.plugin.hooks import BeforeLLMCallInput, BeforeLLMCallResult
from luckbot.domains.session.message_types import build_compaction_summary_message

from .flush_agent import run_memory_flush_agent
from .flush_context import memory_flush_nested
from .paths import ensure_memory_tree
from .types import MemoryPaths

if TYPE_CHECKING:
    from luckbot.domains.memory.embeddings import EmbeddingProvider
    from luckbot.domains.memory.index_db import MemoryIndex

logger = logging.getLogger(__name__)


MERGE_SUMMARIES_PROMPT = (
    "将下列对话摘录合并为一段简短摘要。只保留：已定决策、待办事项、尚未解决的问题、约束条件。"
    "使用中文，避免堆砌原始细节。"
)


def _estimate_tokens(messages: list[Any]) -> int:
    """极粗略 token 估算：总字符/4 + 每条固定开销；仅用于和 ``LUCKBOT_CONTEXT_TOKEN_BUDGET`` 比较。"""
    n = 0
    for m in messages:
        c = getattr(m, "content", "")
        if isinstance(c, str):
            n += len(c)
        elif isinstance(c, list):
            n += 100
        n += 50
    return max(1, n // 4)


def _keep_recent_messages(
    messages: list[BaseMessage], keep: int
) -> list[BaseMessage]:
    """保留列表尾部 K 条（通常含最近 user/assistant，避免摘要后丢失当前话题）。"""
    if keep <= 0 or len(messages) <= keep:
        return list(messages)
    return list(messages[-keep:])


def _flush_transcript_excerpt(messages: list[Any]) -> str:
    """供 flush 使用的 user/assistant 摘录（与旧逻辑一致）。"""
    hist: list[str] = []
    for m in messages[:-1] if len(messages) > 1 else messages:
        if isinstance(m, (HumanMessage, AIMessage)):
            role = "user" if isinstance(m, HumanMessage) else "assistant"
            c = m.content if isinstance(m.content, str) else str(m.content)
            hist.append(f"{role}: {c[:2000]}")
    return "\n".join(hist[-40:])[:12000]


async def maybe_compact_before_llm(
    inp: BeforeLLMCallInput,
    memory_paths: MemoryPaths,
    *,
    memory_index: MemoryIndex | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> BeforeLLMCallResult | None:
    """估算 token 超预算时：可选 flush → 再对「头部」做 LLM 摘要，与尾部 K 条拼成新链。

    返回 ``None`` 表示不改动消息（未配 budget、未超预算、或截断后长度未变）。
    """
    if memory_flush_nested.get():
        return None

    raw = (os.getenv("LUCKBOT_CONTEXT_TOKEN_BUDGET", "") or "").strip()
    if not raw:
        return None
    try:
        budget = int(raw)
    except ValueError:
        return None
    if budget <= 0:
        return None

    msgs = inp.messages
    est = _estimate_tokens(msgs)
    if est < budget:
        return None

    keep = env_int("LUCKBOT_CONTEXT_KEEP_RECENT", 10)

    add_event(
        "memory.compaction.trigger",
        attributes={
            "budget": budget,
            "keep_recent": keep,
            "message_count": len(msgs),
            "estimated_tokens": est,
        },
    )

    ensure_memory_tree(memory_paths)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tail = _flush_transcript_excerpt(msgs)

    if os.getenv("LUCKBOT_MEMORY_FLUSH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        if memory_index is None or embedding_provider is None:
            logger.warning(
                "Memory flush 需要 MemoryIndex 与 EmbeddingProvider，当前缺失，已跳过 flush"
            )
            add_event("memory.compaction.flush.skipped")
        else:
            tok = memory_flush_nested.set(True)
            try:
                max_steps = env_int("LUCKBOT_MEMORY_FLUSH_MAX_STEPS", 12)
                with start_span(
                    "memory.flush_agent",
                    attributes={"memory.flush.max_steps": max_steps, "memory.day": day},
                ):
                    await run_memory_flush_agent(
                        tail,
                        memory_paths,
                        memory_index,
                        embedding_provider,
                        max_steps=max_steps,
                        day=day,
                    )
            except Exception as e:
                logger.warning("Memory flush agent 失败: %s", e)
                record_exception(e)
            finally:
                memory_flush_nested.reset(tok)

    trimmed = _keep_recent_messages(msgs, keep)
    if len(trimmed) == len(msgs):
        add_event("memory.compaction.merge.skipped")
        return None

    try:
        llm = build_llm()
        hist = []
        for m in msgs[: -keep] if keep > 0 else msgs:
            if isinstance(m, (HumanMessage, AIMessage)):
                role = "u" if isinstance(m, HumanMessage) else "a"
                c = m.content if isinstance(m.content, str) else str(m.content)
                hist.append(f"{role}:{c[:1500]}")
        blob = "\n".join(hist[-60:])
        async with start_langsmith_run(
            "memory.compaction.merge",
            run_type="llm",
            inputs={"messages": blob[:14000], "day": day},
        ) as merge_run:
            with start_span("memory.compaction.merge"):
                resp = await llm.ainvoke(
                    [
                        SystemMessage(content=MERGE_SUMMARIES_PROMPT),
                        HumanMessage(content=blob[:14000]),
                    ]
                )
                summary = resp.content if isinstance(resp.content, str) else str(resp.content)
                merge_run.end(outputs={"summary": summary})
        summary_msg = build_compaction_summary_message(summary)
        new_chain: list[BaseMessage] = [summary_msg] + trimmed
        add_event(
            "memory.compaction.done",
            attributes={"kept_messages": len(trimmed), "summary_chars": len(summary.strip())},
        )
        return BeforeLLMCallResult(messages=new_chain)
    except Exception as e:
        logger.warning("摘要压缩失败，仅截断: %s", e)
        record_exception(e)
        return BeforeLLMCallResult(messages=trimmed)
