"""TracerPlugin：运行可观测性插件。

挂载到全部 6 个生命周期 hook 上被动采集信息，
在 after_run 时通过 renderer 输出完整调用链路摘要。
"""

from __future__ import annotations

from agent.plugin.base import LuckbotPlugin, PluginContext
from agent.plugin.hooks import (
    AfterLLMCallInput,
    AfterRunInput,
    AfterToolCallInput,
    BeforeLLMCallInput,
    BeforeRunInput,
    BeforeToolCallInput,
)
from .collector import RunTraceCollector
from . import renderer


class TracerPlugin(LuckbotPlugin):
    name = "luckbot/built-in-tracer"
    version = "0.1.0"

    def __init__(self) -> None:
        self._collector = RunTraceCollector()

    async def initialize(self, ctx: PluginContext) -> None:
        ctx.register_hook("before_run", self._before_run)
        ctx.register_hook("before_llm_call", self._before_llm_call)
        ctx.register_hook("after_llm_call", self._after_llm_call)
        ctx.register_hook("before_tool_call", self._before_tool_call)
        ctx.register_hook("after_tool_call", self._after_tool_call)
        ctx.register_hook("after_run", self._after_run)

    async def _before_run(self, inp: BeforeRunInput) -> None:
        self._collector.reset()
        self._collector.on_before_run(inp.tools, inp.system_prompt)

    async def _before_llm_call(self, inp: BeforeLLMCallInput) -> None:
        self._collector.on_before_llm_call(inp.system_prompt)

    async def _after_llm_call(self, inp: AfterLLMCallInput) -> None:
        self._collector.on_after_llm_call(inp.response, inp.usage)

    async def _before_tool_call(self, inp: BeforeToolCallInput) -> None:
        self._collector.on_before_tool_call(
            inp.name, inp.args, inp.tool_call_id,
        )

    async def _after_tool_call(self, inp: AfterToolCallInput) -> None:
        self._collector.on_after_tool_call(
            inp.name, inp.args, inp.output, inp.tool_call_id,
        )

    async def _after_run(self, inp: AfterRunInput) -> None:
        self._collector.on_after_run()
        renderer.render(self._collector.trace)
