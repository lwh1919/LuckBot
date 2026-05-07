"""MCP client 与工具加载。"""

from __future__ import annotations

import atexit
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, TextIO

from luckbot.core.observability import log_exception

logger = logging.getLogger(__name__)

_STDIO_STDERR_SILENCED = False
_STDIO_ERRLOG: TextIO | None = None


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

    if _has_stdio_server(servers):
        silence_stdio_server_stderr()

    result = MCPBuildResult()
    for server_name, server_cfg in servers.items():
        server_result = await _load_server_tools(
            MultiServerMCPClient,
            server_name,
            server_cfg,
        )
        result.tools.update(server_result.tools)
        result.clients.extend(server_result.clients)

    return result


def silence_stdio_server_stderr() -> None:
    """让 langchain MCP stdio transport 不把 server stderr 直连当前终端。"""
    global _STDIO_STDERR_SILENCED
    if _STDIO_STDERR_SILENCED:
        return
    try:
        import langchain_mcp_adapters.sessions as sessions  # type: ignore[import-untyped]
    except ImportError:
        return

    original = sessions.stdio_client
    if getattr(original, "_luckbot_stderr_silenced", False):
        _STDIO_STDERR_SILENCED = True
        return

    @asynccontextmanager
    async def quiet_stdio_client(server: Any, errlog: TextIO | None = None):
        del errlog
        async with original(server, errlog=_stdio_errlog()) as streams:
            yield streams

    quiet_stdio_client._luckbot_stderr_silenced = True  # type: ignore[attr-defined]
    sessions.stdio_client = quiet_stdio_client
    _STDIO_STDERR_SILENCED = True


async def _load_server_tools(
    client_cls: Any,
    server_name: str,
    server_cfg: Any,
) -> MCPBuildResult:
    if not isinstance(server_cfg, dict):
        logger.warning("MCP server「%s」配置不是 dict，跳过", server_name)
        return MCPBuildResult()

    transport = str(server_cfg.get("transport", "http")).lower()
    conn = normalize_server_config(server_name, transport, server_cfg)
    if conn is None:
        return MCPBuildResult()

    client = None
    try:
        client = client_cls({server_name: conn})
        tools = await client.get_tools()
    except Exception as exc:
        if client is not None:
            await close_mcp_client(client)
        log_exception(logger, "mcp.server_load_failed", exc, server_name=server_name)
        return MCPBuildResult()

    tool_map = {_prefixed_tool_name(server_name, tool): tool for tool in tools}
    return MCPBuildResult(tools=tool_map, clients=[client])


def _prefixed_tool_name(server_name: str, tool: Any) -> str:
    name = f"mcp_{server_name}_{tool.name}"
    tool.name = name
    return name


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
        except Exception as exc:
            log_exception(
                logger,
                "mcp.client_close_failed",
                exc,
                method_name=method_name,
            )
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
        _copy_optional(cfg, result, ("headers",))
        return result

    if transport == "stdio":
        command = cfg.get("command")
        if not command:
            logger.warning("MCP server「%s」缺少 command，跳过", name)
            return None
        result = {"transport": "stdio", "command": command}
        _copy_optional(cfg, result, ("args", "env", "cwd"))
        return result

    logger.warning("MCP server「%s」的 transport「%s」不支持，跳过", name, transport)
    return None


def _has_stdio_server(servers: dict[str, Any]) -> bool:
    for server_cfg in servers.values():
        if (
            isinstance(server_cfg, dict)
            and str(server_cfg.get("transport", "http")).lower() == "stdio"
        ):
            return True
    return False


def _stdio_errlog() -> TextIO:
    global _STDIO_ERRLOG
    if _STDIO_ERRLOG is None or _STDIO_ERRLOG.closed:
        _STDIO_ERRLOG = open(os.devnull, "w", encoding="utf-8")
        atexit.register(_close_stdio_errlog)
    return _STDIO_ERRLOG


def _close_stdio_errlog() -> None:
    if _STDIO_ERRLOG is not None and not _STDIO_ERRLOG.closed:
        _STDIO_ERRLOG.close()


def _copy_optional(
    source: dict[str, Any],
    target: dict[str, Any],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        value = source.get(key)
        if value is not None:
            target[key] = value
