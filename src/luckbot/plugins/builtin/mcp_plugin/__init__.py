"""内置 MCP 插件：把 domain 层提供的 MCP 工具挂到 runtime。"""

from __future__ import annotations

from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.plugin.hooks import BeforeRunInput, BeforeRunResult
from luckbot.domains.mcp import DEFAULT_MCP_CONFIG_PATH, MCPToolRegistry


class MCPPlugin(LuckbotPlugin):
    """从 JSON 配置发现 MCP server 并注册其工具。"""

    name = "luckbot/built-in-mcp"
    version = "0.1.0"

    def __init__(self, config_path: str = DEFAULT_MCP_CONFIG_PATH) -> None:
        self._registry = MCPToolRegistry(config_path)

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_run", self._before_run)

    async def destroy(self, ctx: PluginContext) -> None:  # noqa: ARG002
        await self._registry.close()

    async def _before_run(self, inp: BeforeRunInput) -> BeforeRunResult | None:
        mcp_tools = await self._registry.get_tools()
        if not mcp_tools:
            return None
        merged_tools = dict(inp.tools)
        merged_tools.update(mcp_tools)
        return BeforeRunResult(tools=merged_tools)
