"""MCP 配置读取与刷新策略。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from luckbot.core.config import resolve_project_path

logger = logging.getLogger(__name__)

DEFAULT_MCP_CONFIG_PATH = ".luckbot/mcp.json"


@dataclass(slots=True)
class MCPConfigSnapshot:
    path: str
    exists: bool
    mtime: float | None
    servers: dict[str, Any] | None = None
    error: Exception | None = None


def resolve_mcp_config_path(config_path: str = DEFAULT_MCP_CONFIG_PATH) -> str:
    return resolve_project_path(config_path)


def read_mcp_config(config_path: str = DEFAULT_MCP_CONFIG_PATH) -> MCPConfigSnapshot:
    path = resolve_mcp_config_path(config_path)
    if not os.path.exists(path):
        return MCPConfigSnapshot(path=path, exists=False, mtime=None)

    try:
        mtime = os.stat(path).st_mtime
    except OSError as exc:
        return MCPConfigSnapshot(path=path, exists=True, mtime=None, error=exc)

    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return MCPConfigSnapshot(path=path, exists=True, mtime=mtime, error=exc)

    servers = raw.get("servers")
    if not isinstance(servers, dict):
        servers = None
    return MCPConfigSnapshot(path=path, exists=True, mtime=mtime, servers=servers)


def refresh_policy() -> str:
    raw = (os.getenv("LUCKBOT_MCP_REFRESH_POLICY") or "config").strip().lower()
    if raw in {"config", "always"}:
        return raw
    logger.warning(
        "未知的 LUCKBOT_MCP_REFRESH_POLICY=%s，回退为 config", raw or "<empty>"
    )
    return "config"
