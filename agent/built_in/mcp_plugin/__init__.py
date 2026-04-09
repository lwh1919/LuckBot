"""内置 MCP 插件：读取 ``.luckbot/mcp.json`` 并暴露 MCP 工具。

配置文件用 mtime 做指纹：未改动时单次 ``os.stat()``；变更后重建 MCP client 与工具。

依赖 ``langchain-mcp-adapters`` 将 MCP 工具转为 LangChain ``BaseTool``。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from agent.plugin.base import LuckbotPlugin, PluginContext
from agent.plugin.hooks import BeforeRunInput, BeforeRunResult

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = ".luckbot/mcp.json"


class MCPPlugin(LuckbotPlugin):
    """从 JSON 配置发现 MCP server 并注册其工具。"""

    name = "luckbot/built-in-mcp"
    version = "0.1.0"

    def __init__(self, config_path: str = _DEFAULT_CONFIG_PATH) -> None:
        self._config_path = config_path
        self._config_mtime: float | None = None
        self._cached_tools: dict[str, Any] = {}

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_run", self._before_run)

    async def destroy(self, ctx: PluginContext) -> None:  # noqa: ARG002
        await self._close_clients()

    # -- hook ----------------------------------------------------------------

    async def _before_run(self, inp: BeforeRunInput) -> BeforeRunResult | None:
        if not os.path.exists(self._config_path):
            return None

        mtime = os.stat(self._config_path).st_mtime
        if mtime == self._config_mtime and self._cached_tools:
            merged_tools = dict(inp.tools)
            merged_tools.update(self._cached_tools)
            return BeforeRunResult(tools=merged_tools)

        try:
            with open(self._config_path, encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.exception("读取 MCP 配置失败 %s", self._config_path)
            return None

        servers = config.get("servers")
        if not isinstance(servers, dict):
            logger.warning("MCP 配置缺少有效的「servers」对象")
            return None
        if not servers:
            logger.warning("MCP 配置中的「servers」为空")
            return None

        await self._close_clients()
        self._cached_tools = await _build_mcp_tools(servers)
        self._config_mtime = mtime

        if not self._cached_tools:
            return None
        merged_tools = dict(inp.tools)
        merged_tools.update(self._cached_tools)
        return BeforeRunResult(tools=merged_tools)

    # -- 内部 ---------------------------------------------------------------

    async def _close_clients(self) -> None:
        self._cached_tools.clear()


# -- MCP 工具加载 ---------------------------------------------------------


async def _build_mcp_tools(
    servers: dict[str, Any],
) -> dict[str, Any]:
    """连接各 MCP server，收集 LangChain ``BaseTool``。

    依赖 ``langchain-mcp-adapters``；未安装时打警告并返回空 dict。
    """
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "未安装 langchain-mcp-adapters，MCP 插件已禁用。"
            "请执行: pip install langchain-mcp-adapters"
        )
        return {}

    tool_map: dict[str, Any] = {}

    for server_name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            logger.warning("MCP server「%s」配置不是 dict，跳过", server_name)
            continue

        transport = str(server_cfg.get("transport", "http")).lower()
        try:
            conn: dict[str, Any] = _normalize_server_config(server_name, transport, server_cfg)
            if conn is None:
                continue

            client = MultiServerMCPClient({server_name: conn})
            tools = await client.get_tools()

            for t in tools:
                prefixed = f"mcp_{server_name}_{t.name}"
                t.name = prefixed
                tool_map[prefixed] = t

            logger.info(
                "已加载 MCP server「%s」(%d 个工具)", server_name, len(tools)
            )
        except Exception:
            logger.exception("加载 MCP server「%s」失败", server_name)

    logger.info("MCP 插件共 %d 个工具", len(tool_map))
    return tool_map


def _normalize_server_config(
    name: str, transport: str, cfg: dict[str, Any]
) -> dict[str, Any] | None:
    """将 ``mcp.json`` 结构转为 ``MultiServerMCPClient`` 所需参数。"""
    if transport in ("http", "sse"):
        url = cfg.get("url")
        if not url:
            logger.warning("MCP server「%s」缺少 url，跳过", name)
            return None
        result: dict[str, Any] = {"transport": transport, "url": url}
        headers = cfg.get("headers")
        if headers is not None:
            result["headers"] = headers
        return result

    if transport == "stdio":
        command = cfg.get("command")
        if not command:
            logger.warning("MCP server「%s」缺少 command，跳过", name)
            return None
        result = {"transport": "stdio", "command": command}
        args = cfg.get("args")
        if args is not None:
            result["args"] = args
        env = cfg.get("env")
        if env is not None:
            result["env"] = env
        cwd = cfg.get("cwd")
        if cwd is not None:
            result["cwd"] = cwd
        return result

    logger.warning("MCP server「%s」的 transport「%s」不支持，跳过", name, transport)
    return None
