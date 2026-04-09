"""内置插件自动发现：扫描 agent/built_in/ 下所有 *_plugin/ 包。

约定与外部插件完全相同：
  每个 *_plugin/ 包的 __init__.py 中定义一个 LuckbotPlugin 子类，
  discover_builtin_plugins() 自动实例化并返回。

内置与外部的唯一区别：内置包已在 Python 包路径中，用
  importlib.import_module("agent.built_in.<name>") 直接导入；
  外部包由 PluginManager._scan_dir 将父目录加入 sys.path 后导入。
"""

from __future__ import annotations

import importlib
import logging
import os

from agent.plugin.manager import _find_plugin_class

logger = logging.getLogger(__name__)

_BUILTIN_DIR = os.path.dirname(__file__)


def discover_builtin_plugins():
    """扫描 agent/built_in/ 下的 *_plugin/ 包，返回自动实例化的插件列表。"""
    from agent.plugin.base import LuckbotPlugin  # 局部 import 避免循环

    plugins: list[LuckbotPlugin] = []

    trace_enabled = os.getenv("LUCKBOT_TRACE", "1") == "1"

    for name in sorted(os.listdir(_BUILTIN_DIR)):
        if not name.endswith("_plugin"):
            continue
        if name == "tracer_plugin" and not trace_enabled:
            continue
        pkg_path = os.path.join(_BUILTIN_DIR, name)
        if not os.path.isdir(pkg_path):
            continue
        if not os.path.isfile(os.path.join(pkg_path, "__init__.py")):
            continue

        try:
            mod = importlib.import_module(f"agent.built_in.{name}")
        except Exception:
            logger.exception("导入内置插件包 %s 失败", name)
            continue

        plugin_cls = _find_plugin_class(mod)
        if plugin_cls is None:
            logger.warning("内置插件包 %s 中未找到 LuckbotPlugin 子类", name)
            continue

        try:
            plugins.append(plugin_cls())
            logger.info("已发现内置插件: %s", name)
        except Exception:
            logger.exception("实例化内置插件 %s 失败", name)

    return plugins
