"""会话归档：将当前动态会话提炼为静态记忆 Markdown。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from luckbot.core.config.env_parse import env_int
from luckbot.core.llm.client import build_llm
from luckbot.domains.memory.paths import ensure_memory_tree, resolve_memory_write_path
from luckbot.domains.memory.types import MemoryPaths
from luckbot.domains.session.state import resolve_session
from luckbot.domains.session.transcript import load_transcript_messages


@dataclass
class SessionArchivePayload:
    slug: str
    summary: str
    key_points: list[str]
    conversation_highlights: list[str]
    action_items: list[str]


def _archive_enabled() -> bool:
    return os.getenv("LUCKBOT_SESSION_PERSIST", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _slugify(text: str) -> str:
    lowered = text.strip().lower()
    lowered = re.sub(r"[^a-z0-9\u4e00-\u9fff\s-]+", " ", lowered)
    lowered = re.sub(r"\s+", "-", lowered).strip("-")
    if not lowered:
        return ""
    if re.search(r"[a-z0-9]", lowered):
        return lowered[:48].strip("-")
    return ""


def _fallback_slug(excerpt: str) -> str:
    for line in excerpt.splitlines():
        slug = _slugify(line.replace("User:", "").replace("Assistant:", ""))
        if slug:
            return slug
    digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()[:8]
    return f"session-{digest}"


def _recent_human_assistant_messages(
    messages: list[BaseMessage],
    *,
    limit: int,
) -> list[BaseMessage]:
    filtered = [
        m
        for m in messages
        if isinstance(m, (HumanMessage, AIMessage))
    ]
    if limit <= 0:
        return filtered
    return filtered[-limit:]


def _excerpt_from_messages(messages: list[BaseMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            lines.append(f"User: {text}")
        elif isinstance(msg, AIMessage):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            lines.append(f"Assistant: {text}")
    return "\n".join(lines).strip()


def _fallback_payload(excerpt: str) -> SessionArchivePayload:
    user_lines = [
        line[len("User: ") :].strip()
        for line in excerpt.splitlines()
        if line.startswith("User: ")
    ]
    assistant_lines = [
        line[len("Assistant: ") :].strip()
        for line in excerpt.splitlines()
        if line.startswith("Assistant: ")
    ]
    summary = user_lines[0] if user_lines else "本次会话讨论了一组需要后续检索的事项。"
    key_points = [line for line in user_lines[:3] if line]
    highlights: list[str] = []
    for u, a in zip(user_lines[:2], assistant_lines[:2]):
        highlights.append(f"User: {u}")
        highlights.append(f"Assistant: {a}")
    action_items = [line for line in assistant_lines[:2] if line]
    return SessionArchivePayload(
        slug=_fallback_slug(excerpt),
        summary=summary[:300],
        key_points=key_points or [summary[:120]],
        conversation_highlights=highlights or excerpt.splitlines()[:4],
        action_items=action_items,
    )


def _strip_code_fence(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()


async def _build_archive_payload(
    *,
    session_key: str,
    excerpt: str,
    day: str,
) -> SessionArchivePayload:
    fallback = _fallback_payload(excerpt)
    llm = build_llm()
    prompt = (
        "请根据下列会话摘录生成用于长期记忆归档的 JSON。\n"
        "只返回 JSON，不要加 markdown 代码块。\n"
        '字段: slug, summary, key_points, conversation_highlights, action_items。\n'
        "要求:\n"
        "- slug: 2 到 6 个英文或拼音短词，用连字符连接，适合作为文件名。\n"
        "- summary: 1 段简洁中文总结。\n"
        "- key_points: 3 到 6 条。\n"
        "- conversation_highlights: 2 到 6 条原意摘录。\n"
        "- action_items: 0 到 5 条待办或后续事项。\n"
        f"- 当前会话键: {session_key}\n"
        f"- 当前 UTC 日期: {day}\n\n"
        "会话摘录:\n"
        f"{excerpt}"
    )
    try:
        resp = await llm.ainvoke(prompt)
        raw = resp.content if isinstance(resp.content, str) else str(resp.content)
        parsed = json.loads(_strip_code_fence(raw))
        slug = _slugify(str(parsed.get("slug") or "")) or fallback.slug
        summary = str(parsed.get("summary") or "").strip() or fallback.summary
        key_points = [
            str(x).strip()
            for x in list(parsed.get("key_points") or [])
            if str(x).strip()
        ] or fallback.key_points
        highlights = [
            str(x).strip()
            for x in list(parsed.get("conversation_highlights") or [])
            if str(x).strip()
        ] or fallback.conversation_highlights
        action_items = [
            str(x).strip()
            for x in list(parsed.get("action_items") or [])
            if str(x).strip()
        ]
        return SessionArchivePayload(
            slug=slug,
            summary=summary,
            key_points=key_points[:6],
            conversation_highlights=highlights[:6],
            action_items=action_items[:5],
        )
    except Exception:
        return fallback


def _render_archive_markdown(
    payload: SessionArchivePayload,
    *,
    session_key: str,
    day: str,
    created_at: str,
) -> str:
    key_points = "\n".join(f"- {item}" for item in payload.key_points)
    highlights = "\n".join(f"- {item}" for item in payload.conversation_highlights)
    actions = "\n".join(f"- {item}" for item in payload.action_items)
    body = [
        f"# Session Memory: {day}",
        "",
        f"- Session Key: `{session_key}`",
        f"- Archived At: {created_at}",
        f"- Slug: `{payload.slug}`",
        "",
        "## Summary",
        payload.summary.strip(),
        "",
        "## Key Points",
        key_points or "- 无",
        "",
        "## Conversation Highlights",
        highlights or "- 无",
    ]
    if payload.action_items:
        body.extend(["", "## Action Items", actions])
    body.append("")
    return "\n".join(body)


async def archive_last_session_to_markdown(
    session_key: str,
    paths: MemoryPaths,
    messages: list[Any] | None = None,
    *,
    session_id: str | None = None,
) -> str | None:
    """将当前活动会话归档到 memory/YYYY-MM-DD-{slug}.md。"""
    ensure_memory_tree(paths)
    limit = env_int("LUCKBOT_SESSION_MEMORY_LAST_N", 15)

    if messages:
        base_messages = [m for m in messages if isinstance(m, BaseMessage)]
    elif _archive_enabled():
        active_session_id = session_id or resolve_session(session_key).session_id
        base_messages = load_transcript_messages(active_session_id)
    else:
        base_messages = []

    recent = _recent_human_assistant_messages(base_messages, limit=limit)
    excerpt = _excerpt_from_messages(recent)
    if not excerpt:
        return None

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    payload = await _build_archive_payload(
        session_key=session_key,
        excerpt=excerpt[:14000],
        day=day,
    )
    rel_path = f"memory/{day}-{payload.slug}.md"
    dst = resolve_memory_write_path(rel_path, paths)
    if dst is None:
        raise RuntimeError(f"归档路径非法: {rel_path}")
    markdown = _render_archive_markdown(
        payload,
        session_key=session_key,
        day=day,
        created_at=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    )
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(markdown, encoding="utf-8")
    return rel_path


__all__ = ["archive_last_session_to_markdown"]
