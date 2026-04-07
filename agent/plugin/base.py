"""LuckbotPlugin 抽象基类与 PluginContext：所有插件的扩展契约。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

VALID_HOOK_NAMES = frozenset(
    {
        "before_run",
        "after_run",
        "before_llm_call",
        "after_llm_call",
        "before_tool_call",
        "after_tool_call",
    }
)


class PluginContext:
    """在 :meth:`LuckbotPlugin.initialize` 期间向插件暴露的能力。"""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}
        self._hooks: dict[str, list[Callable[..., Any]]] = {n: [] for n in VALID_HOOK_NAMES}
        self._services: dict[str, Any] = {}

    # -- 工具注册 ---------------------------------------------------

    def register_tool(self, name: str, tool: Any) -> None:
        self._tools[name] = tool

    def get_tools(self) -> dict[str, Any]:
        return dict(self._tools)

    # -- hook 注册 ---------------------------------------------------

    def register_hook(self, hook_name: str, handler: Callable[..., Any]) -> None:
        if hook_name not in VALID_HOOK_NAMES:
            raise ValueError(
                f"未知的 hook「{hook_name}」。合法名称: {sorted(VALID_HOOK_NAMES)}"
            )
        self._hooks[hook_name].append(handler)

    def get_hooks(self, hook_name: str) -> list[Callable[..., Any]]:
        return list(self._hooks.get(hook_name, []))

    # -- 服务注册 ------------------------------------------------

    def register_service(self, name: str, service: Any) -> None:
        self._services[name] = service

    def get_service(self, name: str) -> Any | None:
        return self._services.get(name)


class LuckbotPlugin(ABC):
    """所有 LuckBot 插件（内置与外部）的基类。"""

    name: str
    version: str = "0.1.0"
    dependencies: list[str] = []

    @abstractmethod
    async def initialize(self, ctx: PluginContext) -> None:
        """在 *ctx* 上注册工具、hook 与服务。"""

    async def destroy(self, ctx: PluginContext) -> None:  # noqa: ARG002
        """可选：清理资源（关闭连接、刷盘等）。"""
