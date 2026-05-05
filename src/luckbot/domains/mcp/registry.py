"""MCP 工具注册表：缓存配置、client 和工具。"""

from __future__ import annotations

import logging
from typing import Any

from .config import DEFAULT_MCP_CONFIG_PATH, read_mcp_config, refresh_policy
from .loader import build_mcp_tools, close_mcp_client

logger = logging.getLogger(__name__)


class MCPToolRegistry:
    """按配置刷新并缓存 MCP 工具。"""

    def __init__(self, config_path: str = DEFAULT_MCP_CONFIG_PATH) -> None:
        self._config_path = config_path
        self._config_mtime: float | None = None
        self._cached_tools: dict[str, Any] = {}
        self._clients: list[Any] = []

    async def get_tools(self) -> dict[str, Any]:
        snapshot = read_mcp_config(self._config_path)
        if not snapshot.exists:
            await self.close()
            self._config_mtime = None
            return {}

        if snapshot.error is not None:
            await self.close()
            self._config_mtime = snapshot.mtime
            logger.exception("读取 MCP 配置失败 %s", snapshot.path, exc_info=snapshot.error)
            return {}

        if (
            refresh_policy() == "config"
            and snapshot.mtime == self._config_mtime
            and self._cached_tools
        ):
            return dict(self._cached_tools)

        if snapshot.servers is None:
            await self.close()
            self._config_mtime = snapshot.mtime
            logger.warning("MCP 配置缺少有效的「servers」对象")
            return {}

        if not snapshot.servers:
            await self.close()
            self._config_mtime = snapshot.mtime
            logger.warning("MCP 配置中的「servers」为空")
            return {}

        await self.close()
        build = await build_mcp_tools(snapshot.servers)
        self._cached_tools = build.tools
        self._clients = build.clients
        self._config_mtime = snapshot.mtime
        return dict(self._cached_tools)

    async def close(self) -> None:
        self._cached_tools.clear()
        for client in reversed(self._clients):
            await close_mcp_client(client)
        self._clients.clear()
