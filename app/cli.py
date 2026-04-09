"""LuckBot CLI：交互式多轮对话入口。

不解析命令行参数；可调项通过环境变量配置（见 .env.example）。
CLI 只负责 I/O，完整 hook 生命周期由 agent_loop 内部管理。
"""

from __future__ import annotations

import asyncio
import os
import logging

_log_level = os.getenv("LUCKBOT_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(level=getattr(logging, _log_level, logging.WARNING))

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from agent.built_in import discover_builtin_plugins
from agent.loop.agent_loop import DEFAULT_MAX_STEPS, agent_loop
from agent.plugin.manager import PluginManager

_console = Console()

SYSTEM_PROMPT = (
    "你是 LuckBot，务实的编程助手。用简短清晰的中文回答。\n"
    "需要查文件、搜代码、跑命令时，应主动调用工具；各工具的用途与参数以请求中附带的工具说明为准，勿用 shell 重复实现已有工具能力。\n\n"
    "## 工具选用原则（摘要）\n"
    "- 读改写文件、列目录、搜代码：用专用工具；终端类（git/构建/测试）用 bash；"
    "大量临时文件或怕弄脏仓库时用 sandbox_run（产物可写 $OUTPUT_DIR）。\n"
    "- Skill：可复用流程先 skill_load，按需 skill_select_docs / skill_get_doc，"
    "跑技能目录内脚本用 skill_run；其余步骤按技能说明配合上述工具完成。\n"
    "- Skill 执行：多行脚本通过 skill_run 的 script_content 参数传入，"
    "禁止用 write 在项目目录创建临时脚本文件。\n"
)


def _max_steps_from_env() -> int:
    """ReAct 单轮最大步数：环境变量 LUCKBOT_RECURSION_LIMIT，非法则回退默认值。"""
    raw = os.getenv("LUCKBOT_RECURSION_LIMIT", str(DEFAULT_MAX_STEPS))
    n = int(raw.strip())
    return max(1, n)


def _print_banner() -> None:
    title = Text("LuckBot", style="bold cyan")
    subtitle = Text("务实的编程助手  •  输入 exit 退出", style="dim")
    banner = Text.assemble(title, "  ", subtitle)
    _console.print(Panel(banner, border_style="cyan", expand=False, padding=(0, 2)))
    _console.print()


def _print_result(result: str) -> None:
    _console.print(Rule(style="dim"))
    _console.print(Markdown(result))
    _console.print()


async def async_main() -> None:
    load_dotenv()
    max_steps = _max_steps_from_env()

    _print_banner()

    pm = PluginManager()
    await pm.initialize(
        plugins=discover_builtin_plugins(),
        plugin_dirs=[".luckbot/plugins"],
    )

    while True:
        try:
            user_input = _console.input("[bold green]你>[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            _console.print("\n[dim]已退出。[/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in {"quit", "exit"}:
            _console.print("[dim]已退出。[/dim]")
            break

        result = await agent_loop(
            user_input,
            pm,
            system_prompt=SYSTEM_PROMPT,
            max_steps=max_steps,
        )
        _print_result(result)

    await pm.destroy()


def main() -> None:
    # 同步入口托管异步主流程，保持外部调用方式简单（python main.py）。
    asyncio.run(async_main())
