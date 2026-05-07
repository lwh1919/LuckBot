"""LangChain 消息与 JSONL transcript 互转。

每行 JSON 一条「记录」：含 ``timestamp``、``run_id``、以及内嵌 ``message``（role/content 等）。
默认不序列化 ``SystemMessage``（避免与运行时 system prompt 重复落盘），
仅对白名单内部消息（如 compaction 摘要）例外。

与 ``state.transcript_path(session_id)`` 配合：文件名使用 ``session_id``，而非 ``session_key``。
本模块不再维护 ``sessions/*.md`` 检索视图；需要显式回看旧会话时，可把
``sessions/<session_id>.jsonl`` 解析成文本返回。规范读取 path 为 ``session:<session_id>``。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from luckbot.domains.session.message_types import (
    COMPACTION_SUMMARY_KIND,
    COMPACTION_SUMMARY_PREFIX,
    build_compaction_summary_message,
    is_compaction_summary_message,
)
from luckbot.domains.session.state import transcript_path

SESSION_READ_PATH_PREFIX = "session:"


def _as_text(content: Any) -> str:
    """LangChain 的 content 可能是 str 或富媒体结构，落盘统一为 str。"""
    return content if isinstance(content, str) else str(content)


def _normalize_tool_call(c: dict[str, Any]) -> dict[str, Any]:
    """OpenAI 风格 tool_call 与 JSON 往返共用，保证 id/name/args 形状稳定。"""
    return {
        "id": str(c.get("id", "")),
        "name": str(c.get("name", "")),
        "args": dict(c.get("args") or {}),
    }


def _tool_calls_from_message(msg: AIMessage) -> list[dict[str, Any]]:
    """从 AIMessage 提取可 JSON 序列化的 tool_calls 列表。"""
    tc = getattr(msg, "tool_calls", None) or []
    return [_normalize_tool_call(c) for c in tc if isinstance(c, dict)]


def _tool_call_ids(msg: AIMessage) -> list[str]:
    calls = _tool_calls_from_message(msg)
    out: list[str] = []
    for call in calls:
        tid = call["id"].strip()
        if tid:
            out.append(tid)
    return out


def normalize_transcript_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """过滤不满足 LLM tool-call 配对约束的 transcript 消息。

    ToolMessage 只能紧跟在带 tool_calls 的 AIMessage 后，并且必须完整覆盖该
    AIMessage 声明的 tool_call id。非法工具调用片段整组丢弃，避免旧 transcript
    污染下一轮 LLM 请求。
    """
    out: list[BaseMessage] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if isinstance(msg, HumanMessage):
            out.append(msg)
            i += 1
            continue
        if isinstance(msg, SystemMessage):
            if is_compaction_summary_message(msg):
                out.append(msg)
            i += 1
            continue
        if isinstance(msg, ToolMessage):
            i += 1
            continue
        if isinstance(msg, AIMessage):
            expected_ids = _tool_call_ids(msg)
            if not expected_ids:
                out.append(msg)
                i += 1
                continue

            expected = set(expected_ids)
            seen: set[str] = set()
            tools: list[ToolMessage] = []
            invalid = len(expected) != len(expected_ids)
            j = i + 1
            while j < n and isinstance(messages[j], ToolMessage):
                tool_msg = messages[j]
                tid = str(getattr(tool_msg, "tool_call_id", "") or "").strip()
                if not tid or tid not in expected or tid in seen:
                    invalid = True
                seen.add(tid)
                tools.append(tool_msg)
                j += 1

            if not invalid and seen == expected:
                out.append(msg)
                out.extend(tools)
            i = j
            continue
        i += 1
    return out


def session_read_path(session_id: str) -> str:
    """旧会话显式读取的规范 path。"""
    sid = session_id.strip()
    return f"{SESSION_READ_PATH_PREFIX}{sid}"


def is_allowed_session_read_path(path: str) -> bool:
    """只允许 ``session:<session_id>``。"""
    return session_read_path_to_session_id(path) is not None


def session_read_path_to_session_id(path: str) -> str | None:
    """从 ``session:<session_id>`` 读取 session_id。"""
    raw = path.strip().replace("\\", "/")
    sid = ""
    if raw.startswith(SESSION_READ_PATH_PREFIX):
        sid = raw[len(SESSION_READ_PATH_PREFIX) :].strip()
    if not sid or "/" in sid or ".." in sid:
        return None
    return sid


def _message_payload(msg: BaseMessage) -> dict[str, Any] | None:
    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": _as_text(msg.content)}
    if isinstance(msg, AIMessage):
        serializable = _tool_calls_from_message(msg)
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": _as_text(msg.content),
        }
        if serializable:
            payload["tool_calls"] = serializable
        return payload
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "content": _as_text(msg.content),
            "tool_call_id": getattr(msg, "tool_call_id", "") or "",
        }
    if is_compaction_summary_message(msg):
        return {
            "role": "system",
            "content": _as_text(msg.content),
            "kind": COMPACTION_SUMMARY_KIND,
        }
    if isinstance(msg, SystemMessage):
        return None
    return {"role": msg.__class__.__name__, "content": str(msg)}


def _msg_to_record(msg: BaseMessage, run_id: str) -> dict[str, Any] | None:
    """单条 BaseMessage → 写入 JSONL 的一条 dict（含同一 run 内共享的 run_id）。"""
    payload = _message_payload(msg)
    if payload is None:
        return None
    return {
        "timestamp": time.time(),
        "run_id": run_id,
        "message": payload,
    }


def _record_to_message(rec: dict[str, Any]) -> BaseMessage | None:
    """JSONL 反序列化后的一条 record → BaseMessage；未知 role 返回 None（跳过该行）。"""
    inner = rec.get("message")
    if not isinstance(inner, dict):
        return None
    role = inner.get("role")
    content = _as_text(inner.get("content", "") or "")
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        raw_tc = inner.get("tool_calls") or []
        tool_calls = [
            _normalize_tool_call(c)
            for c in (raw_tc if isinstance(raw_tc, list) else [])
            if isinstance(c, dict)
        ]
        if tool_calls:
            return AIMessage(content=content, tool_calls=tool_calls)
        return AIMessage(content=content)
    if role == "tool":
        tid = str(inner.get("tool_call_id") or "")
        return ToolMessage(content=content, tool_call_id=tid)
    if role == "system":
        kind = str(inner.get("kind") or "")
        if kind == COMPACTION_SUMMARY_KIND:
            if content.startswith(f"{COMPACTION_SUMMARY_PREFIX}\n"):
                summary = content.split("\n", 1)[1]
            elif content == COMPACTION_SUMMARY_PREFIX:
                summary = ""
            else:
                summary = content
            return build_compaction_summary_message(summary)
        return None
    return None


def load_transcript_messages(session_id: str) -> list[BaseMessage]:
    """读取会话 JSONL，按行顺序还原为消息列表；文件不存在或整文件无法解析时返回 []。"""
    path = transcript_path(session_id)
    if not path.is_file():
        return []
    out: list[BaseMessage] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if isinstance(rec, dict):
                m = _record_to_message(rec)
                if m is not None:
                    out.append(m)
    except (OSError, json.JSONDecodeError):
        return []
    return normalize_transcript_messages(out)


def build_transcript_read_view(messages: list[BaseMessage]) -> str:
    """旧会话显式读取视图：仅保留 user / assistant 文本。"""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            lines.append(f"User: {_as_text(msg.content)}")
        elif isinstance(msg, AIMessage):
            lines.append(f"Assistant: {_as_text(msg.content)}")
        elif isinstance(msg, SystemMessage) and is_compaction_summary_message(msg):
            lines.append(f"System: {_as_text(msg.content)}")
    return "\n".join(lines).strip()


def load_transcript_read_view(session_id: str) -> str:
    """从 JSONL 渲染出供显式读取的会话文本视图。"""
    return build_transcript_read_view(load_transcript_messages(session_id))


def messages_to_jsonl_lines(
    messages: list[Any],
    *,
    run_id: str,
    start_index: int,
) -> list[str]:
    """将 ``messages[start_index:]`` 转为 JSONL 行（无换行符的 JSON 字符串）。

    ``start_index`` 通常设为「磁盘已持久化条数」，避免同一轮消息重复追加。
    ``run_id`` 为空时会生成 UUID，便于在文件中区分同一轮 agent 产生的多行。
    """
    rid = run_id.strip() or str(uuid.uuid4())
    lines: list[str] = []
    normalized = normalize_transcript_messages(
        [msg for msg in messages if isinstance(msg, BaseMessage)]
    )
    for msg in normalized[start_index:]:
        if not isinstance(msg, BaseMessage):
            continue
        rec = _msg_to_record(msg, rid)
        if rec is not None:
            lines.append(json.dumps(rec, ensure_ascii=False))
    return lines


def append_transcript_lines(session_id: str, lines: list[str]) -> None:
    """将已序列化的 JSON 行追加到对应 ``session_id`` 的 JSONL 文件末尾。"""
    if not lines:
        return
    path = transcript_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.writelines(f"{line}\n" for line in lines)


def messages_to_export_dicts(messages: list[Any]) -> list[dict[str, Any]]:
    """将内存中的消息转为可 ``json.dumps`` 的列表（供 CLI ``/json`` 等）。"""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, BaseMessage):
            continue
        payload = _message_payload(msg)
        if payload is not None:
            out.append(payload)
    return out


def rewrite_transcript_messages(session_id: str, messages: list[Any]) -> None:
    """用当前内存中的完整消息列表覆盖对应 JSONL（例如上下文压缩后列表短于已持久化条数）。"""
    rid = str(uuid.uuid4())
    lines = messages_to_jsonl_lines(messages, run_id=rid, start_index=0)
    path = transcript_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines) + ("\n" if lines else "")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
