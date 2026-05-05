"""会话状态：磁盘路径、session_key 索引、上下文预算 env（占位）。

磁盘布局（在 ``resolve_state_dir()`` 之下）::

    sessions/
        sessions.json          # session_key → { session_id, updated_at, owner_id, ... }
        <session_id>.jsonl     # 该会话的 JSONL transcript（由 transcript 模块读写）

``session_key`` 是环境/业务侧的逻辑名（如 ``LUCKBOT_SESSION``）；``session_id`` 为 UUID，
用作 transcript 文件名。索引与 JSONL 由内置 SessionPlugin 等在 hooks 里驱动。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from luckbot.core.config import resolve_project_path

logger = logging.getLogger(__name__)

def resolve_state_dir() -> Path:
    """LuckBot 持久化根目录。

    优先级：
    1. ``LUCKBOT_STATE_DIR``
    2. ``<project>/.luckbot/state``

    当项目内 state 目录尚未建立、但检测到旧的 ``~/.luckbot`` 运行态数据时，
    会把缺失文件复制到项目内目录，避免迁移后出现“记忆/会话为空”的错觉。
    """
    raw = os.getenv("LUCKBOT_STATE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    state_dir = Path(resolve_project_path(".luckbot/state"))
    _seed_project_state_from_legacy(state_dir)
    return state_dir


def legacy_state_dir() -> Path:
    """旧版默认运行态目录。"""
    return (Path.home() / ".luckbot").resolve()


def _seed_project_state_from_legacy(project_state_dir: Path) -> None:
    legacy_dir = legacy_state_dir()
    if legacy_dir == project_state_dir or not legacy_dir.is_dir():
        return
    try:
        project_state_dir.mkdir(parents=True, exist_ok=True)
        _copy_missing_tree(legacy_dir, project_state_dir)
    except OSError as exc:
        logger.warning("同步旧版运行态目录失败: %s", exc)


def _copy_missing_tree(src: Path, dst: Path) -> None:
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_missing_tree(child, target)
            continue
        if target.exists():
            continue
        shutil.copy2(child, target)


def sessions_dir() -> Path:
    """存放 ``sessions.json`` 与各会话 ``*.jsonl`` 的目录。"""
    return resolve_state_dir() / "sessions"


def sessions_index_path() -> Path:
    """session_key → 元数据 的 JSON 索引路径。"""
    return sessions_dir() / "sessions.json"


def transcript_path(session_id: str) -> Path:
    """给定 ``session_id``，返回对应该会话的 JSONL 文件路径。"""
    return sessions_dir() / f"{session_id}.jsonl"


# --- 预留：压缩/裁剪时可读 LUCKBOT_CONTEXT_TOKEN_BUDGET、LUCKBOT_CONTEXT_KEEP_RECENT ---


def _positive_int_from_env(name: str) -> int | None:
    """解析环境变量为正整数；未设置、非数字、≤0 时返回 None。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
        return n if n > 0 else None
    except ValueError:
        return None


def context_token_budget_from_env() -> int | None:
    """预留：超过该 token 数可触发上下文压缩（由上层实现）。"""
    return _positive_int_from_env("LUCKBOT_CONTEXT_TOKEN_BUDGET")


def context_keep_recent_from_env() -> int | None:
    """预留：压缩后至少保留最近 K 条消息（由上层实现）。"""
    return _positive_int_from_env("LUCKBOT_CONTEXT_KEEP_RECENT")


def _owner_id_default() -> str:
    return (os.getenv("LUCKBOT_OWNER_ID", "") or "local").strip() or "local"


# --- session_key ↔ session_id ---


@dataclass
class SessionMeta:
    """单次逻辑会话的元数据；写回索引时使用 ``dataclasses.asdict``。"""

    session_id: str  # UUID，与 transcript 文件名一致
    session_key: str  # 索引中的键，如 default 或 LUCKBOT_SESSION
    updated_at: float = 0.0  # time.time()，用于排序/清理
    owner_id: str = "local"  # 多用户时可区分租户；记忆检索等可与此关联

    @classmethod
    def from_json(cls, session_key: str, data: dict[str, Any]) -> SessionMeta:
        """从索引条目中恢复；``session_key`` 以调用者提供的键为准。"""
        return cls(
            session_id=str(data.get("session_id") or ""),
            session_key=session_key,
            updated_at=float(data.get("updated_at") or 0.0),
            owner_id=str(data.get("owner_id") or "local"),
        )


def _read_index(path: Path) -> dict[str, dict[str, Any]]:
    """读取索引；文件缺失或 JSON 损坏时返回空 dict，不抛异常。"""
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _write_index(path: Path, index: dict[str, dict[str, Any]]) -> None:
    """整文件重写索引（体量小）；写入前确保父目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_session(session_key: str, owner_id: str | None = None) -> SessionMeta:
    """按 session_key 查找或新建 SessionMeta，并刷新 ``updated_at`` 写回索引。

    空或空白 key 会规范为 ``"default"``。每次调用都会持久化最新的 ``updated_at``。
    """
    key = (session_key or "default").strip() or "default"
    path = sessions_index_path()
    index = _read_index(path)
    now = time.time()
    entry = index.get(key)
    if entry and entry.get("session_id"):
        meta = SessionMeta.from_json(key, entry)
    else:
        meta = SessionMeta(
            session_id=str(uuid.uuid4()),
            session_key=key,
            updated_at=0.0,
            owner_id=(owner_id or _owner_id_default()).strip() or "local",
        )
    if owner_id is not None:
        meta.owner_id = owner_id.strip() or "local"
    meta.updated_at = now
    index[key] = asdict(meta)
    _write_index(path, index)
    return meta


def rotate_session(session_key: str, owner_id: str | None = None) -> SessionMeta:
    """为给定 session_key 生成新的活动 session_id，并写回索引。"""
    key = (session_key or "default").strip() or "default"
    path = sessions_index_path()
    index = _read_index(path)
    meta = SessionMeta(
        session_id=str(uuid.uuid4()),
        session_key=key,
        updated_at=time.time(),
        owner_id=(owner_id or _owner_id_default()).strip() or "local",
    )
    index[key] = asdict(meta)
    _write_index(path, index)
    return meta


def touch_session_updated(session_id: str, session_key: str) -> None:
    """仅当索引中 ``session_key`` 对应的 ``session_id`` 一致时，更新 ``updated_at``。

    避免错用 key 覆盖其它会话；适合 after_run 等已持有稳定 id/key 的场景。
    """
    path = sessions_index_path()
    index = _read_index(path)
    entry = index.get(session_key)
    if entry and str(entry.get("session_id")) == session_id:
        entry["updated_at"] = time.time()
        index[session_key] = entry
        _write_index(path, index)
