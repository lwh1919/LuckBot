"""LuckBot CLI：交互式对话与共享命令入口。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Any

_log_level = os.getenv("LUCKBOT_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(level=getattr(logging, _log_level, logging.WARNING))

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from luckbot.plugins.builtin import discover_builtin_plugins
from luckbot.core.config import resolve_project_path
from luckbot.core.config.env_parse import env_int
from luckbot.core.runtime.agent_loop import DEFAULT_MAX_STEPS, agent_loop
from luckbot.domains.memory.paths import clear_memory_store, resolve_memory_paths
from luckbot.core.observability import init_observability
from luckbot.core.plugin.manager import PluginManager
from luckbot.domains.session import build_local_session_key, normalize_session_name
from luckbot.domains.session.state import resolve_session
from luckbot.domains.session.transcript import load_transcript_messages
from luckbot.application.commands import CommandContext, execute_command
from luckbot.application.prompt import SYSTEM_PROMPT
from luckbot.application.services import (
    GatewayClientError,
    read_gateway_logs,
    read_gateway_status,
    resolve_gateway_base_url,
    send_gateway_command,
    send_gateway_turn,
    start_gateway_process,
    stop_gateway_process,
)

_console = Console()


def _max_steps_from_env() -> int:
    return max(1, env_int("LUCKBOT_RECURSION_LIMIT", DEFAULT_MAX_STEPS))


def _session_persist_enabled() -> bool:
    return os.getenv("LUCKBOT_SESSION_PERSIST", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _local_session_name() -> str:
    return normalize_session_name(os.getenv("LUCKBOT_SESSION") or "default")


def _local_owner_id() -> str:
    return (os.getenv("LUCKBOT_OWNER_ID") or "local").strip() or "local"


def _gateway_auto_start_enabled() -> bool:
    return os.getenv("LUCKBOT_GATEWAY_AUTOSTART", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _initial_conversation_history(session_key: str) -> list[Any]:
    if not _session_persist_enabled():
        return []
    meta = resolve_session(session_key)
    return load_transcript_messages(meta.session_id)


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


async def _build_plugin_manager() -> PluginManager:
    pm = PluginManager()
    await pm.initialize(
        plugins=discover_builtin_plugins(),
        plugin_dirs=[resolve_project_path(".luckbot/plugins")],
    )
    return pm


async def _handle_user_input(
    user_input: str,
    *,
    pm: PluginManager,
    conversation_history: list[Any],
    session_key: str,
    owner_id: str | None = None,
    max_steps: int,
) -> tuple[str, list[Any], bool]:
    command_result = await execute_command(
        user_input,
        CommandContext(
            transport="cli",
            plugin_manager=pm,
            conversation_history=conversation_history,
            session_key=session_key,
            owner_id=owner_id,
        ),
    )
    if command_result.handled:
        return (
            command_result.final_text,
            command_result.updated_conversation_history
            if command_result.updated_conversation_history is not None
            else conversation_history,
            True,
        )

    result, updated_history = await agent_loop(
        user_input,
        pm,
        system_prompt=SYSTEM_PROMPT,
        conversation_history=conversation_history,
        session_key=session_key,
        owner_id=owner_id,
        max_steps=max_steps,
    )
    return result, updated_history, False


async def async_chat() -> None:
    load_dotenv()
    init_observability(component="cli")
    session_name = _local_session_name()
    os.environ["LUCKBOT_SESSION"] = session_name
    session_key = build_local_session_key(session_name)

    _print_banner(session_name, mode="local")
    pm = await _build_plugin_manager()
    conversation_history: list[Any] = _initial_conversation_history(session_key)
    if conversation_history:
        _console.print(f"[dim]已从磁盘恢复会话消息 {len(conversation_history)} 条。[/dim]")

    try:
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

            try:
                result, conversation_history, handled = await _handle_user_input(
                    user_input,
                    pm=pm,
                    conversation_history=conversation_history,
                    session_key=session_key,
                    max_steps=_max_steps_from_env(),
                )
            except Exception as exc:
                _console.print(f"[red]执行失败:[/red] {exc}")
                continue

            if handled:
                _print_command_result(result)
            else:
                _print_agent_result(result)
    finally:
        await pm.destroy()


async def async_chat_gateway() -> None:
    load_dotenv()
    init_observability(component="cli-gateway")
    _ensure_gateway_running()
    session_name = _local_session_name()
    _print_banner(session_name, mode="gateway")

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

        try:
            if user_input.startswith("/"):
                result = await asyncio.to_thread(
                    send_gateway_command,
                    user_input,
                    session_name=session_name,
                    owner_id=_local_owner_id(),
                )
            else:
                result = await asyncio.to_thread(
                    send_gateway_turn,
                    user_input,
                    session_name=session_name,
                    owner_id=_local_owner_id(),
                )
        except GatewayClientError as exc:
            _console.print(f"[red]执行失败:[/red] {exc}")
            continue

        if user_input.startswith("/"):
            _print_command_result(result.final_text)
        else:
            _print_agent_result(result.final_text)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LuckBot：交互对话与本地记忆管理（配置见 .env / .env.example）。",
        epilog="不带子命令时进入交互对话。",
    )
    sub = parser.add_subparsers(dest="cmd")

    chat_p = sub.add_parser("chat", help="进入交互式对话。")
    chat_p.add_argument(
        "--gateway",
        action="store_true",
        help="通过正在运行的 gateway 进行对话。",
    )

    ask_p = sub.add_parser("ask", help="执行单次提问。")
    ask_p.add_argument("prompt", nargs="+", help="要发送的 prompt。")
    ask_p.add_argument(
        "--gateway",
        action="store_true",
        help="通过正在运行的 gateway 执行单次提问。",
    )

    cmd_p = sub.add_parser("cmd", help="执行共享 slash 命令。")
    cmd_p.add_argument("command", nargs="+", help="如 /skill list")
    cmd_p.add_argument(
        "--gateway",
        action="store_true",
        help="通过正在运行的 gateway 执行 slash 命令。",
    )

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

    gateway = sub.add_parser("gateway", help="gateway 进程管理。")
    gateway_sub = gateway.add_subparsers(dest="gateway_action", required=True)
    gateway_sub.add_parser("serve", help="启动 gateway 服务。")
    gateway_sub.add_parser("start", help="后台启动 gateway 服务。")
    gateway_sub.add_parser("stop", help="停止后台 gateway 服务。")
    gateway_sub.add_parser("status", help="查看 gateway 状态。")
    logs_p = gateway_sub.add_parser("logs", help="查看 gateway 日志。")
    logs_p.add_argument(
        "--follow",
        action="store_true",
        help="持续跟随日志输出，Ctrl+C 退出。",
    )
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
    pm = await _build_plugin_manager()
    conversation_history = _initial_conversation_history(session_key)
    try:
        result, _history, handled = await _handle_user_input(
            text,
            pm=pm,
            conversation_history=conversation_history,
            session_key=session_key,
            max_steps=_max_steps_from_env(),
        )
    finally:
        await pm.destroy()

    if handled:
        _print_command_result(result)
    else:
        _print_agent_result(result)
    return 0


async def _run_once_gateway(text: str, *, as_command: bool) -> int:
    _ensure_gateway_running()
    session_name = _local_session_name()
    try:
        if as_command:
            result = await asyncio.to_thread(
                send_gateway_command,
                text,
                session_name=session_name,
                owner_id=_local_owner_id(),
            )
            _print_command_result(result.final_text)
        else:
            result = await asyncio.to_thread(
                send_gateway_turn,
                text,
                session_name=session_name,
                owner_id=_local_owner_id(),
            )
            _print_agent_result(result.final_text)
    except GatewayClientError as exc:
        raise SystemExit(f"gateway 执行失败: {exc}") from exc
    return 0


def _ensure_gateway_running() -> None:
    status = read_gateway_status()
    if status.running:
        return
    if not _gateway_auto_start_enabled():
        raise SystemExit(
            "gateway 未运行，请先执行 `luckbot gateway start` 或 `python main.py gateway start`。"
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


def main() -> None:
    load_dotenv()
    parser = _build_arg_parser()
    args = parser.parse_args()

    if getattr(args, "cmd", None) == "memory" and args.memory_action == "clear":
        _run_memory_clear(yes=bool(args.yes))
        return

    if getattr(args, "cmd", None) == "ask":
        prompt = " ".join(args.prompt).strip()
        if not prompt:
            raise SystemExit("prompt 不能为空")
        if bool(args.gateway):
            raise SystemExit(asyncio.run(_run_once_gateway(prompt, as_command=False)))
        raise SystemExit(asyncio.run(_run_once(prompt)))

    if getattr(args, "cmd", None) == "cmd":
        command = " ".join(args.command).strip()
        if not command.startswith("/"):
            raise SystemExit("cmd 仅接受 slash 命令，例如 /skill list")
        if bool(args.gateway):
            raise SystemExit(asyncio.run(_run_once_gateway(command, as_command=True)))
        raise SystemExit(asyncio.run(_run_once(command)))

    if getattr(args, "cmd", None) == "gateway":
        action = args.gateway_action
        if action == "serve":
            from luckbot.adapters.gateway.app import main as gateway_main

            gateway_main()
            return
        if action == "start":
            status = start_gateway_process()
            _console.print(f"[green]gateway 已启动[/green] PID={status.pid} URL={resolve_gateway_base_url()}")
            return
        if action == "stop":
            status = stop_gateway_process()
            _console.print(
                "[green]gateway 已停止[/green]" if not status.running else "[yellow]gateway 仍在运行[/yellow]"
            )
            return
        if action == "status":
            _run_gateway_status()
            return
        if action == "logs":
            _run_gateway_logs(follow=bool(args.follow))
            return

    if getattr(args, "cmd", None) == "chat" and bool(args.gateway):
        asyncio.run(async_chat_gateway())
        return

    asyncio.run(async_chat())
