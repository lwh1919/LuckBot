"""LuckBot 运行态消息类型辅助。

仅为内部特殊消息提供稳定标记，避免与普通顶层 SystemMessage 混淆。
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, SystemMessage

COMPACTION_SUMMARY_KIND = "luckbot_compaction_summary"
COMPACTION_SUMMARY_PREFIX = "[此前对话已压缩摘要]"
_INTERNAL_KIND_KEY = "luckbot_internal_kind"


def build_compaction_summary_message(summary: str) -> SystemMessage:
    """构造可被 transcript 白名单持久化的压缩摘要消息。"""
    body = summary.strip()
    return SystemMessage(
        content=f"{COMPACTION_SUMMARY_PREFIX}\n{body}" if body else COMPACTION_SUMMARY_PREFIX,
        additional_kwargs={_INTERNAL_KIND_KEY: COMPACTION_SUMMARY_KIND},
    )


def is_compaction_summary_message(msg: BaseMessage | object) -> bool:
    """判断消息是否为 LuckBot 内部 compaction 摘要。"""
    if not isinstance(msg, SystemMessage):
        return False
    meta = getattr(msg, "additional_kwargs", None)
    if not isinstance(meta, dict):
        return False
    return meta.get(_INTERNAL_KIND_KEY) == COMPACTION_SUMMARY_KIND

