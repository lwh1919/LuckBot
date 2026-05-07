"""Session domain public API."""

from luckbot.domains.session.state import (
    SessionMeta,
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
from luckbot.domains.session.identity import default_owner_id
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
    "default_owner_id",
    "load_transcript_messages",
    "messages_to_jsonl_lines",
    "normalize_session_name",
    "rewrite_transcript_messages",
    "resolve_session",
    "resolve_state_dir",
    "sessions_dir",
    "sessions_index_path",
]
