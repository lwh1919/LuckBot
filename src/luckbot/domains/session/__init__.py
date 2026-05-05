"""对外聚合 ``agent.session.state`` 与 ``agent.session.transcript`` 的公开 API。

需要 ``touch_session_updated`` 等未列入 ``__all__`` 的符号时，请从子模块直接导入。
"""

from luckbot.domains.session.state import (
    SessionMeta,
    context_keep_recent_from_env,
    context_token_budget_from_env,
    resolve_session,
    resolve_state_dir,
    sessions_dir,
    sessions_index_path,
)
from luckbot.domains.session.keys import (
    build_gateway_cli_session_key,
    build_local_session_key,
    normalize_session_name,
)
from luckbot.domains.session.transcript import (
    append_transcript_lines,
    load_transcript_messages,
    messages_to_jsonl_lines,
    rewrite_transcript_messages,
)

__all__ = [
    "SessionMeta",
    "append_transcript_lines",
    "build_gateway_cli_session_key",
    "build_local_session_key",
    "context_keep_recent_from_env",
    "context_token_budget_from_env",
    "load_transcript_messages",
    "messages_to_jsonl_lines",
    "normalize_session_name",
    "rewrite_transcript_messages",
    "resolve_session",
    "resolve_state_dir",
    "sessions_dir",
    "sessions_index_path",
]
