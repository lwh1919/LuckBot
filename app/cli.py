"""LuckBot CLI：交互式多轮对话入口。

不解析命令行参数；可调项通过环境变量配置（见 .env.example）。
CLI 只负责 I/O，完整 hook 生命周期由 agent_loop 内部管理。
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from agent.built_in import MCPPlugin, SkillsPlugin
from agent.loop.agent_loop import DEFAULT_MAX_STEPS, agent_loop
from agent.plugin.manager import PluginManager
from agent.tools.builtin import get_builtin_tools

SYSTEM_PROMPT = (
    "你是 LuckBot，一个务实的编程助手。\n"
    "请使用简短清晰的中文回答。\n"
    "当工具能提升准确性时，优先调用工具。"
)


def _max_steps_from_env() -> int:
    """ReAct 单轮最大步数：环境变量 LUCKBOT_RECURSION_LIMIT，非法则回退默认值。"""
    raw = os.getenv("LUCKBOT_RECURSION_LIMIT", str(DEFAULT_MAX_STEPS))
    try:
        n = int(raw.strip())
        if n >= 1:
            return n
        return DEFAULT_MAX_STEPS
    except ValueError:
        return DEFAULT_MAX_STEPS


async def async_main() -> None:
    load_dotenv()
    max_steps = _max_steps_from_env()

    pm = PluginManager()
    await pm.initialize(
        plugins=[SkillsPlugin(), MCPPlugin()],
        plugin_dirs=[".luckbot/plugins"],
    )

    builtin_tools = {t.name: t for t in get_builtin_tools()}

    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if not user_input:
            continue

        if user_input.lower() in {"quit", "exit"}:
            print("已退出。")
            break

        result = await agent_loop(
            user_input,
            pm,
            system_prompt=SYSTEM_PROMPT,
            tools=builtin_tools,
            max_steps=max_steps,
        )
        print(result)

    await pm.destroy()


def main() -> None:
    # 同步入口托管异步主流程，保持外部调用方式简单（python main.py）。
    asyncio.run(async_main())
