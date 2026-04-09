"""内置基础工具插件：bash / sandbox_run / read / write / edit / grep / ls。

通过 PluginContext 注册工具，并挂载 before_tool_call
安全拦截 hook。安全策略由 SafetyConfig 驱动。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.plugin.base import LuckbotPlugin, PluginContext
from agent.plugin.hooks import BeforeToolCallInput, BeforeToolCallResult

from .bash import build_bash_tool
from .file_edit import build_edit_tool
from .file_read import build_read_tool
from .file_write import build_write_tool
from .grep import build_grep_tool
from .ls import build_ls_tool
from .sandbox_run import build_sandbox_run_tool
from .safety import SafetyConfig

logger = logging.getLogger(__name__)


class ToolsPlugin(LuckbotPlugin):
    """提供 Agent 的基础文件与终端操作能力。"""

    name = "luckbot/built-in-tools"
    version = "0.1.0"

    def __init__(self, safety: SafetyConfig | None = None) -> None:
        self._safety = safety or SafetyConfig()

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_tool("bash", build_bash_tool(self._safety))
        ctx.register_tool("sandbox_run", build_sandbox_run_tool(self._safety))
        ctx.register_tool("read", build_read_tool())
        ctx.register_tool("write", build_write_tool())
        ctx.register_tool("edit", build_edit_tool())
        ctx.register_tool("grep", build_grep_tool())
        ctx.register_tool("ls", build_ls_tool())

        ctx.register_hook("before_tool_call", self._safety_hook)

        logger.info(
            "ToolsPlugin 已注册 7 个基础工具 "
            "(bash_timeout=%ds, sandbox_timeout=%ds)",
            self._safety.bash_timeout,
            self._safety.sandbox_timeout,
        )

    async def _safety_hook(
        self, inp: BeforeToolCallInput
    ) -> BeforeToolCallResult | None:
        """before_tool_call: 拦截写操作（当 allow_write_tools=False 时）。"""
        if not self._safety.allow_write_tools and inp.name in ("write", "edit"):
            raise RuntimeError(
                f"[已拦截] 工具 {inp.name} 已被安全策略禁用（allow_write_tools=false）"
            )
        return None
