"""MCP client 与工具加载。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MCPBuildResult:
    tools: dict[str, Any] = field(default_factory=dict)
    clients: list[Any] = field(default_factory=list)


async def build_mcp_tools(servers: dict[str, Any]) -> MCPBuildResult:
    """连接各 MCP server，收集 LangChain ``BaseTool``。"""
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "未安装 langchain-mcp-adapters，MCP 插件已禁用。"
            "请执行: pip install langchain-mcp-adapters"
        )
        return MCPBuildResult()

    tool_map: dict[str, Any] = {}
    clients: list[Any] = []

    for server_name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            logger.warning("MCP server「%s」配置不是 dict，跳过", server_name)
            continue

        transport = str(server_cfg.get("transport", "http")).lower()
        try:
            conn = normalize_server_config(server_name, transport, server_cfg)
            if conn is None:
                continue

            client = MultiServerMCPClient({server_name: conn})
            tools = await client.get_tools()
            clients.append(client)

            for tool in tools:
                prefixed = f"mcp_{server_name}_{tool.name}"
                tool.name = prefixed
                tool_map[prefixed] = tool

            logger.info("已加载 MCP server「%s」(%d 个工具)", server_name, len(tools))
        except Exception:
            logger.exception("加载 MCP server「%s」失败", server_name)

    logger.info("MCP 插件共 %d 个工具", len(tool_map))
    return MCPBuildResult(tools=tool_map, clients=clients)


async def close_mcp_client(client: Any) -> None:
    """尽力关闭 MCP client，适配不同 adapter 版本的关闭接口。"""
    for method_name in ("aclose", "close", "disconnect", "cleanup"):
        method = getattr(client, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if isawaitable(result):
                await result
        except Exception:
            logger.exception("关闭 MCP client 失败（method=%s）", method_name)
        return


def normalize_server_config(
    name: str,
    transport: str,
    cfg: dict[str, Any],
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
