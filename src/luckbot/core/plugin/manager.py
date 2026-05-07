"""PluginManager：收集、排序并初始化全部 LuckBot 插件。"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from typing import Any, Callable

from luckbot.core.config import resolve_project_path
from luckbot.core.observability import log_exception
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext

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
            raise RuntimeError(
                "PluginManager 已处于 initialized 状态；如需重建，请先调用 destroy()"
            )

        if plugins is not None:
            all_plugins: list[LuckbotPlugin] = list(plugins)
        else:
            all_plugins = []

        if plugin_dirs:
            for d in plugin_dirs:
                all_plugins.extend(self._scan_dir(d))

        sorted_plugins = self._topo_sort(all_plugins)

        for plugin in sorted_plugins:
            await plugin.initialize(self._ctx)
            self._plugins.append(plugin)

        self._initialised = True

    async def destroy(self) -> None:
        for plugin in reversed(self._plugins):
            try:
                await plugin.destroy(self._ctx)
            except Exception as exc:
                log_exception(
                    logger,
                    "plugin.destroy_failed",
                    exc,
                    plugin_name=plugin.name,
                )
        self._plugins.clear()
        self._ctx.reset()
        self._initialised = False

    def get_tools(self) -> dict[str, Any]:
        return self._ctx.get_tools()

    def get_hooks(self, hook_name: str) -> list[Callable[..., Any]]:
        return self._ctx.get_hooks(hook_name)

    def get_service(self, name: str) -> Any | None:
        return self._ctx.get_service(name)

    def list_plugins(self) -> list[tuple[str, str]]:
        """已加载插件的 ``(name, version)`` 列表（拓扑初始化顺序）。"""
        return [(p.name, getattr(p, "version", "0.0.0")) for p in self._plugins]

    # -- 目录扫描 ----------------------------------------------------------

    @staticmethod
    def _scan_dir(directory: str) -> list[LuckbotPlugin]:
        """扫描目录下所有 *_plugin/ 包，加载并返回插件实例列表。

        约定：每个 *_plugin/ 包的 __init__.py 中定义一个 LuckbotPlugin 子类。
        """
        resolved = resolve_project_path(directory)
        if not os.path.isdir(resolved):
            return []

        found: list[LuckbotPlugin] = []
        for name in sorted(os.listdir(resolved)):
            if not name.endswith("_plugin"):
                continue
            pkg_path = os.path.join(resolved, name)
            if not os.path.isdir(pkg_path):
                continue
            if not os.path.isfile(os.path.join(pkg_path, "__init__.py")):
                continue
            try:
                plugin = _load_plugin_package(resolved, name)
                found.append(plugin)
            except Exception as exc:
                log_exception(
                    logger,
                    "plugin.load_external_failed",
                    exc,
                    plugin_path=pkg_path,
                )
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
                if dep_plugin is None:
                    raise RuntimeError(
                        f"插件 {p.name} 缺少依赖 {dep}，无法初始化"
                    )
                visit(dep_plugin)
            visiting.discard(p.name)
            visited.add(p.name)
            sorted_list.append(p)

        for plugin in plugins:
            visit(plugin)
        return sorted_list


def _load_plugin_package(parent_dir: str, pkg_name: str) -> LuckbotPlugin:
    """动态加载外部 *_plugin/ 包，在 __init__.py 中查找 LuckbotPlugin 子类并实例化。

    将 parent_dir 加入 sys.path，使包内的相对 import 正常工作。
    """
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    mod = importlib.import_module(pkg_name)
    plugin_cls = _find_plugin_class(mod)
    if plugin_cls is None:
        raise AttributeError(
            f"插件包 {pkg_name} 的 __init__.py 中未找到 LuckbotPlugin 子类"
        )
    return plugin_cls()


def _find_plugin_class(module: object) -> type[LuckbotPlugin] | None:
    """在模块中查找第一个 LuckbotPlugin 子类（排除基类本身）。"""
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, LuckbotPlugin)
            and attr is not LuckbotPlugin
        ):
            return attr
    return None
