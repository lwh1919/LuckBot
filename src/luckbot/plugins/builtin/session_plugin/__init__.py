"""会话持久化：按 session_key 将多轮消息写入 state_dir 下 sessions/*.jsonl。

与 MemoryPlugin 无关：此处只负责对话 transcript 的加载/追加；具体路径由
agent.session.state / transcript 解析（默认如 <project>/.luckbot/state/sessions/）。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from luckbot.core.observability import increment_counter, start_span
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.plugin.hooks import (
    AfterRunInput,
    BeforeLLMCallInput,
    BeforeRunInput,
    BeforeRunResult,
)
from luckbot.domains.session.state import resolve_session, rotate_session, touch_session_updated
from luckbot.domains.session.transcript import (
    append_transcript_lines,
    load_transcript_messages,
    messages_to_jsonl_lines,
    rewrite_transcript_messages,
)


def _persist_enabled() -> bool:
    """LUCKBOT_SESSION_PERSIST 非 0/false/no/off 时落盘会话。"""
    return os.getenv("LUCKBOT_SESSION_PERSIST", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


class SessionPlugin(LuckbotPlugin):
    """before_run 恢复历史，after_run 将本轮消息追加为 JSONL。"""

    name = "session"
    version = "0.1.0"
    dependencies = ["memory"]

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self._session_key = "default"  # env 默认值；运行时可由 hook 输入覆盖
        self._owner_id = "local"
        self._session_id = ""  # resolve_session 得到的稳定 id，用于文件名
        self._persisted_len = 0  # 已持久化消息条数，用于 JSONL 行号连续
        self._force_full_transcript_rewrite = False

    async def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._session_key = (
            os.getenv("LUCKBOT_SESSION", "").strip() or "default"
        )
        self._owner_id = (os.getenv("LUCKBOT_OWNER_ID", "").strip() or "local")
        ctx.register_hook("before_run", self._before_run)
        ctx.register_hook("before_llm_call", self._before_llm)
        ctx.register_hook("after_run", self._after_run)
        ctx.register_service("session_flush", self.flush_transcript)
        ctx.register_service("session_begin_new", self.begin_new_session)

    @staticmethod
    def _effective_session_key(session_key: str | None, fallback: str) -> str:
        return (session_key or fallback or "default").strip() or "default"

    @staticmethod
    def _effective_owner_id(owner_id: str | None, fallback: str) -> str:
        return (owner_id or fallback or "local").strip() or "local"

    def _set_identity(
        self,
        *,
        session_key: str | None,
        owner_id: str | None,
    ) -> None:
        self._session_key = self._effective_session_key(session_key, self._session_key)
        self._owner_id = self._effective_owner_id(owner_id, self._owner_id)

    def _span_attrs(
        self,
        *,
        session_key: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, str]:
        return {
            "luckbot.session_key": session_key or self._session_key,
            "luckbot.owner_id": owner_id or self._owner_id,
        }

    def _mark_flushed(self, message_count: int) -> None:
        self._persisted_len = message_count
        touch_session_updated(self._session_id, self._session_key)

    def begin_new_session(
        self,
        *,
        session_key: str | None = None,
        owner_id: str | None = None,
    ) -> str:
        """旋转到新的活动会话，保留旧 transcript 供动态记忆检索。"""
        if not _persist_enabled():
            self._persisted_len = 0
            self._force_full_transcript_rewrite = False
            return "会话持久化已关闭（LUCKBOT_SESSION_PERSIST），仅重置内存会话"
        self._set_identity(session_key=session_key, owner_id=owner_id)
        meta = rotate_session(self._session_key, owner_id=self._owner_id)
        self._session_id = meta.session_id
        self._persisted_len = 0
        self._force_full_transcript_rewrite = False
        touch_session_updated(self._session_id, self._session_key)
        return f"已开始新会话: {self._session_id}"

    async def _before_run(self, inp: BeforeRunInput) -> BeforeRunResult | None:
        if not _persist_enabled():
            return None
        with start_span(
            "session.resolve",
            attributes=self._span_attrs(
                session_key=inp.session_key,
                owner_id=inp.owner_id,
            ),
        ):
            self._set_identity(session_key=inp.session_key, owner_id=inp.owner_id)
            self._force_full_transcript_rewrite = False
            meta = resolve_session(self._session_key, owner_id=self._owner_id)
            self._session_id = meta.session_id
            if len(inp.messages) > 1:
                self._persisted_len = len(inp.messages) - 1
                return None
            loaded = load_transcript_messages(self._session_id)
            if not loaded:
                self._persisted_len = 0
                return None
            self._persisted_len = len(loaded)
            return BeforeRunResult(messages=[*loaded, *inp.messages])

    async def _before_llm(self, inp: BeforeLLMCallInput) -> None:
        """在 Memory等插件完成 before_llm_call 之后注册顺序靠后：检测压缩是否缩短消息链。"""
        if not _persist_enabled() or not self._session_id:
            return
        if self._persisted_len > len(inp.messages):
            self._force_full_transcript_rewrite = True

    async def _after_run(self, inp: AfterRunInput) -> None:
        """增量追加；若本轮发生过上下文压缩导致链变短，则整文件重写为当前内存快照。"""
        if not _persist_enabled():
            return
        self.flush_transcript(
            inp.messages,
            session_key=inp.session_key,
            owner_id=inp.owner_id,
        )
        sync_now = self._ctx.get_service("memory_sync_now") if self._ctx else None
        if callable(sync_now):
            await sync_now()

    def flush_transcript(
        self,
        messages: list[Any],
        *,
        session_key: str | None = None,
        owner_id: str | None = None,
    ) -> str:
        """将 ``messages`` 同步到当前会话 JSONL（与 ``after_run`` 相同规则）。供 CLI ``/save``。"""
        if not _persist_enabled():
            return "会话持久化已关闭（LUCKBOT_SESSION_PERSIST）"
        with start_span(
            "session.flush",
            attributes=self._span_attrs(session_key=session_key, owner_id=owner_id),
        ):
            self._set_identity(session_key=session_key, owner_id=owner_id)
            meta = resolve_session(self._session_key, owner_id=self._owner_id)
            self._session_id = meta.session_id

            if self._force_full_transcript_rewrite or self._persisted_len > len(messages):
                rewrite_transcript_messages(self._session_id, messages)
                self._force_full_transcript_rewrite = False
                n = len(messages)
                self._mark_flushed(n)
                increment_counter("luckbot_session_flush_total", attributes={"mode": "rewrite"})
                return f"已全量写入 {n} 条消息"

            run_id = str(uuid.uuid4())
            lines = messages_to_jsonl_lines(
                messages,
                run_id=run_id,
                start_index=self._persisted_len,
            )
            if lines:
                append_transcript_lines(self._session_id, lines)
            self._mark_flushed(len(messages))
            increment_counter("luckbot_session_flush_total", attributes={"mode": "append"})
            if not lines:
                return "无新消息需保存"
            return f"已追加 {len(lines)} 条消息"


__all__ = ["SessionPlugin"]
