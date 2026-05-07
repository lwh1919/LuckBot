"""LuckBot CLI：交互式对话与共享命令入口。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

_log_level = os.getenv("LUCKBOT_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(level=getattr(logging, _log_level, logging.WARNING))

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from luckbot.core.config.env_parse import env_int
from luckbot.core.runtime import DEFAULT_MAX_STEPS
from luckbot.domains.memory.paths import clear_memory_store, resolve_memory_paths
from luckbot.core.observability import init_observability
from luckbot.domains.session import build_local_session_key, default_owner_id, normalize_session_name
from luckbot.application.gateway import (
    GatewayClientError,
    read_gateway_logs,
    read_gateway_status,
    resolve_gateway_base_url,
    send_gateway_turn,
    start_gateway_process,
    stop_gateway_process,
    use_gateway_base_url,
)
from luckbot.application.turn_runner import AgentTurnRunner
from luckbot.application.turns import IncomingTurn

_console = Console()


@dataclass(slots=True)
class CliTurnResult:
    final_text: str
    conversation_history: list[Any]
    is_command: bool


def _max_steps_from_env() -> int:
    return max(1, env_int("LUCKBOT_RECURSION_LIMIT", DEFAULT_MAX_STEPS))


def _local_session_name() -> str:
    return normalize_session_name(os.getenv("LUCKBOT_SESSION") or "default")


def _local_owner_id() -> str:
    return default_owner_id()


def _gateway_auto_start_enabled() -> bool:
    return os.getenv("LUCKBOT_GATEWAY_AUTOSTART", "1").strip().lower() not in {
        "0",
    }


def _print_banner(session_name: str, *, mode: str = "local") -> None:
    title = Text("LuckBot", style="bold cyan")
    if mode == "gateway":
        subtitle_text = (
            f"务实的编程助手  •  gateway [{resolve_gateway_base_url()}]  •  "
            f"会话 [{session_name}]  •  exit 退出  •  /help 指令"
        )
    else:
        subtitle_text = (
            f"务实的编程助手  •  本地会话 [{session_name}]  •  exit 退出  •  /help 指令"
        )
    subtitle = Text(subtitle_text, style="dim")
    banner = Text.assemble(title, "  ", subtitle)
    _console.print(Panel(banner, border_style="cyan", expand=False, padding=(0, 2)))
    _console.print()


def _print_agent_result(result: str) -> None:
    _console.print(Rule(style="dim"))
    _console.print(Markdown(result))
    _console.print()


def _print_command_result(result: str) -> None:
    _console.print(Rule(style="dim"))
    _console.print(result)
    _console.print()


def _read_user_input() -> str | None:
    try:
        user_input = _console.input("[bold green]你>[/bold green] ").strip()
    except (EOFError, KeyboardInterrupt):
        _console.print("\n[dim]已退出。[/dim]")
        return None

    if user_input.lower() in {"quit", "exit"}:
        _console.print("[dim]已退出。[/dim]")
        return None
    return user_input


def _print_turn_result(result: CliTurnResult) -> None:
    if result.is_command:
        _print_command_result(result.final_text)
    else:
        _print_agent_result(result.final_text)


async def _chat_loop(
    send_turn: Callable[[str], Awaitable[CliTurnResult]],
    *,
    handled_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> None:
    while True:
        user_input = _read_user_input()
        if user_input is None:
            break
        if not user_input:
            continue

        try:
            result = await send_turn(user_input)
        except handled_exceptions as exc:
            _console.print(f"[red]执行失败:[/red] {exc}")
            continue

        _print_turn_result(result)


async def async_chat() -> None:
    load_dotenv()
    init_observability(component="cli")
    session_name = _local_session_name()
    os.environ["LUCKBOT_SESSION"] = session_name
    session_key = build_local_session_key(session_name)

    _print_banner(session_name, mode="local")
    runner = AgentTurnRunner(max_steps=_max_steps_from_env())

    async def _send_local_turn(user_input: str) -> CliTurnResult:
        result = await runner.run_turn(
            IncomingTurn(
                channel="cli",
                transport="local",
                text=user_input,
                session_key=session_key,
                owner_id=_local_owner_id(),
                chat_id=session_name,
                user_id=_local_owner_id(),
            )
        )
        return CliTurnResult(
            final_text=result.final_text,
            conversation_history=result.messages,
            is_command=result.handled_as_command,
        )

    await _chat_loop(_send_local_turn)


async def async_chat_gateway() -> None:
    load_dotenv()
    init_observability(component="cli-gateway")
    session_name = _local_session_name()
    _print_banner(session_name, mode="gateway")

    async def _send_gateway_turn(user_input: str) -> CliTurnResult:
        result = await asyncio.to_thread(
            send_gateway_turn,
            user_input,
            session_name=session_name,
            owner_id=_local_owner_id(),
        )
        return CliTurnResult(
            final_text=result.final_text,
            conversation_history=[],
            is_command=user_input.startswith("/"),
        )

    await _chat_loop(_send_gateway_turn, handled_exceptions=(GatewayClientError,))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LuckBot：交互对话与本地记忆管理（配置见 .env / .env.example）。",
        epilog="不带子命令时进入交互对话。",
    )
    sub = parser.add_subparsers(dest="command")

    ask_p = sub.add_parser("ask", help="执行单次提问。")
    ask_p.add_argument("prompt", nargs="+", help="要发送的 prompt。")
    ask_p.set_defaults(handler=_run_ask_from_args)

    mem = sub.add_parser("memory", help="本地记忆目录与索引")
    mem_sub = mem.add_subparsers(dest="memory_action", required=True)
    clear_p = mem_sub.add_parser(
        "clear",
        help="删除当前 LUCKBOT_OWNER_ID 下 memory 目录，不影响 sessions/*.jsonl。",
    )
    clear_p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="跳过确认（脚本 / 自动化测试用）",
    )
    clear_p.set_defaults(handler=_run_memory_from_args)

    gateway = sub.add_parser(
        "gateway",
        help="进入 gateway 对话或管理 gateway 进程。",
        description="不带参数时启动或复用本地 gateway；传 URL 时连接外部 gateway。",
    )
    gateway.add_argument(
        "target",
        nargs="?",
        help="start / stop / status / logs，或远程 gateway URL。",
    )
    gateway.add_argument(
        "--follow",
        action="store_true",
        help="target 为 logs 时持续跟随日志输出，Ctrl+C 退出。",
    )
    gateway.set_defaults(handler=_run_gateway_from_args)
    return parser


def _run_memory_clear(*, yes: bool) -> None:
    paths = resolve_memory_paths()
    root = paths.memory_root
    if not yes and sys.stdin.isatty():
        _console.print(
            f"[bold]将删除本地记忆目录：[/bold] [cyan]{root}[/cyan]\n"
            "[dim]（含 MEMORY.md、memory/**/*.md、index.sqlite；不删会话 JSONL；"
            "不删 LUCKBOT_MEMORY_EXTRA_PATHS 指向的其他磁盘文件）[/dim]"
        )
        ans = _console.input("确认删除？输入 yes 继续 [y/N]: ").strip().lower()
        if ans not in {"y", "yes"}:
            _console.print("[dim]已取消。[/dim]")
            return
    clear_memory_store(paths)
    _console.print(f"[green]已清空并重建空记忆树：[/green] {root}")


async def _run_once(text: str) -> int:
    session_name = _local_session_name()
    os.environ["LUCKBOT_SESSION"] = session_name
    session_key = build_local_session_key(session_name)
    result = await AgentTurnRunner(max_steps=_max_steps_from_env()).run_turn(
        IncomingTurn(
            channel="cli",
            transport="local",
            text=text,
            session_key=session_key,
            owner_id=_local_owner_id(),
            chat_id=session_name,
            user_id=_local_owner_id(),
        )
    )

    _print_turn_result(
        CliTurnResult(
            final_text=result.final_text,
            conversation_history=result.messages,
            is_command=result.handled_as_command,
        )
    )
    return 0


def _ensure_gateway_running() -> None:
    status = read_gateway_status()
    if status.running:
        return
    if not _gateway_auto_start_enabled():
        raise SystemExit(
            "gateway 未运行，请先执行 `luckbot gateway start`。"
        )
    try:
        start_gateway_process()
    except RuntimeError as exc:
        raise SystemExit(f"gateway 启动失败: {exc}") from exc


def _run_gateway_status() -> None:
    status = read_gateway_status()
    lines = [
        f"运行中: {'yes' if status.running else 'no'}",
        f"PID: {status.pid or '-'}",
        f"Host: {status.host}",
        f"Port: {status.port}",
        f"Channels: {', '.join(status.channels)}",
        f"Log: {status.log_path}",
        f"状态: {status.detail}",
    ]
    if status.started_at is not None:
        lines.append(f"Started At: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(status.started_at))}")
    _console.print("\n".join(lines))


def _run_gateway_logs(*, follow: bool) -> None:
    path, lines = read_gateway_logs()
    _console.print(f"[bold]日志文件:[/bold] {path}")
    if lines:
        _console.print("\n".join(lines))
    else:
        _console.print("[dim]日志为空。[/dim]")
    if not follow:
        return
    _console.print("[dim]开始跟随日志，Ctrl+C 退出。[/dim]")
    try:
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                line = handle.readline()
                if line:
                    _console.print(line.rstrip("\n"))
                    continue
                time.sleep(0.3)
    except FileNotFoundError:
        _console.print("[red]日志文件不存在。[/red]")
    except KeyboardInterrupt:
        _console.print("\n[dim]已停止跟随日志。[/dim]")


def _run_ask_from_args(args: argparse.Namespace) -> None:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("prompt 不能为空")
    raise SystemExit(asyncio.run(_run_once(prompt)))


def _run_memory_from_args(args: argparse.Namespace) -> None:
    if args.memory_action == "clear":
        _run_memory_clear(yes=bool(args.yes))


def _run_gateway_from_args(args: argparse.Namespace) -> None:
    target = (args.target or "").strip()
    if not target:
        _ensure_gateway_running()
        asyncio.run(async_chat_gateway())
        return
    if target == "start":
        status = start_gateway_process()
        _console.print(f"[green]gateway 已启动[/green] PID={status.pid} URL={resolve_gateway_base_url()}")
        return
    if target == "stop":
        status = stop_gateway_process()
        _console.print(
            "[green]gateway 已停止[/green]" if not status.running else "[yellow]gateway 仍在运行[/yellow]"
        )
        return
    if target == "status":
        _run_gateway_status()
        return
    if target == "logs":
        _run_gateway_logs(follow=bool(args.follow))
        return

    use_gateway_base_url(target)
    asyncio.run(async_chat_gateway())


def main() -> None:
    load_dotenv()
    parser = _build_arg_parser()
    args = parser.parse_args()

    handler = getattr(args, "handler", None)
    if handler is not None:
        handler(args)
        return

    asyncio.run(async_chat())
