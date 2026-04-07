"""LuckBot Agent 运行时的插件系统。"""

from agent.plugin.base import LuckbotPlugin, PluginContext
from agent.plugin.hooks import (
    AfterLLMCallInput,
    AfterRunInput,
    AfterToolCallInput,
    AfterToolCallResult,
    BeforeLLMCallInput,
    BeforeLLMCallResult,
    BeforeRunInput,
    BeforeRunResult,
    BeforeToolCallInput,
    BeforeToolCallResult,
)

__all__ = [
    "LuckbotPlugin",
    "PluginContext",
    "BeforeRunInput",
    "BeforeRunResult",
    "BeforeLLMCallInput",
    "BeforeLLMCallResult",
    "AfterLLMCallInput",
    "BeforeToolCallInput",
    "BeforeToolCallResult",
    "AfterToolCallInput",
    "AfterToolCallResult",
    "AfterRunInput",
]
