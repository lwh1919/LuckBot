"""PluginManager：收集、排序并初始化全部 LuckBot 插件。"""

from __future__ import annotations

import importlib.util
import logging
import os
from typing import Any, Callable

from agent.plugin.base import LuckbotPlugin, PluginContext

logger = logging.getLogger(__name__)


class PluginManager:
    """管理插件全生命周期：发现 → 拓扑排序 → 初始化 → 运行时查询。"""

    def __init__(self) -> None:
        self._ctx = PluginContext()
        self._plugins: list[LuckbotPlugin] = []
        self._initialised = False

    # -- 对外 API ----------------------------------------------------------

    async def initialize(
        self,
        plugins: list[LuckbotPlugin] | None = None,
        plugin_dirs: list[str] | None = None,
    ) -> None:
        if self._initialised:
            logger.warning("PluginManager.initialize() 被重复调用")
            return

        if plugins is not None:
            all_plugins: list[LuckbotPlugin] = list(plugins)
        else:
            all_plugins = []

        if plugin_dirs:
            for d in plugin_dirs:
                all_plugins.extend(self._scan_dir(d))

        sorted_plugins = self._topo_sort(all_plugins)

        for plugin in sorted_plugins:
            logger.info("正在初始化插件: %s v%s", plugin.name, plugin.version)
            await plugin.initialize(self._ctx)
            self._plugins.append(plugin)

        self._initialised = True
        logger.info("插件系统就绪（共 %d 个插件）", len(self._plugins))

    async def destroy(self) -> None:
        for plugin in reversed(self._plugins):
            try:
                await plugin.destroy(self._ctx)
            except Exception:
                logger.exception("销毁插件 %s 时出错", plugin.name)
        self._plugins.clear()

    def get_tools(self) -> dict[str, Any]:
        return self._ctx.get_tools()

    def get_hooks(self, hook_name: str) -> list[Callable[..., Any]]:
        return self._ctx.get_hooks(hook_name)

    def get_service(self, name: str) -> Any | None:
        return self._ctx.get_service(name)

    # -- 目录扫描 ----------------------------------------------------------

    @staticmethod
    def _scan_dir(directory: str) -> list[LuckbotPlugin]:
        resolved = os.path.expanduser(directory)
        if not os.path.isdir(resolved):
            logger.debug("插件目录不存在: %s", resolved)
            return []

        found: list[LuckbotPlugin] = []
        for filename in sorted(os.listdir(resolved)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            filepath = os.path.join(resolved, filename)
            try:
                plugin = _load_plugin_file(filepath)
                found.append(plugin)
                logger.info("已从 %s 加载外部插件: %s", filepath, plugin.name)
            except Exception:
                logger.exception("从 %s 加载插件失败", filepath)
        return found

    # -- 依赖感知的拓扑排序 -----------------------------------------------

    @staticmethod
    def _topo_sort(plugins: list[LuckbotPlugin]) -> list[LuckbotPlugin]:
        by_name: dict[str, LuckbotPlugin] = {p.name: p for p in plugins}
        sorted_list: list[LuckbotPlugin] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(p: LuckbotPlugin) -> None:
            if p.name in visited:
                return
            if p.name in visiting:
                raise RuntimeError(f"检测到插件循环依赖: {p.name}")
            visiting.add(p.name)
            for dep in p.dependencies:
                dep_plugin = by_name.get(dep)
                if dep_plugin is not None:
                    visit(dep_plugin)
            visiting.discard(p.name)
            visited.add(p.name)
            sorted_list.append(p)

        for plugin in plugins:
            visit(plugin)
        return sorted_list


def _load_plugin_file(filepath: str) -> LuckbotPlugin:
    """动态加载 *filepath*，返回模块级变量 ``plugin``。"""
    mod_name = os.path.splitext(os.path.basename(filepath))[0]
    spec = importlib.util.spec_from_file_location(f"luckbot_ext.{mod_name}", filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {filepath} 创建模块 spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    plugin = getattr(module, "plugin", None)
    if plugin is None:
        raise AttributeError(
            f"插件文件 {filepath} 必须定义模块级变量「plugin」，且为 LuckbotPlugin 实例"
        )
    if not isinstance(plugin, LuckbotPlugin):
        raise TypeError(
            f"{filepath} 中的「plugin」必须是 LuckbotPlugin 实例，实际为 {type(plugin).__name__}"
        )
    return plugin
