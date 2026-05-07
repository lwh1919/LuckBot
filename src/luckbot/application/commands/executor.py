from __future__ import annotations

import json
from typing import Any

from luckbot.domains.memory.paths import resolve_memory_paths
from luckbot.domains.memory.session_memory import archive_last_session_to_markdown
from luckbot.domains.session.transcript import messages_to_export_dicts

from .parser import tokenize_command
from .registry import render_help_text
from .types import CommandContext, CommandResult, TokenizedCommand


async def execute_command(text: str, ctx: CommandContext) -> CommandResult:
    try:
        tokenized = tokenize_command(text)
    except ValueError as exc:
        return CommandResult(handled=True, final_text=f"命令解析失败: {exc}")
    if tokenized is None:
        return CommandResult(handled=False)
    if not tokenized.name:
        return CommandResult(handled=True, final_text=_unknown_command_text(text))

    if tokenized.name == "help":
        return CommandResult(handled=True, final_text=render_help_text())
    if tokenized.name == "skill":
        return _execute_skill_command(tokenized, ctx)
    if tokenized.name == "mcp":
        return await _execute_mcp_command(tokenized, ctx)
    if tokenized.name == "plugin":
        return _execute_plugin_command(tokenized, ctx)
    if tokenized.name == "session":
        return await _execute_session_command(tokenized, ctx)
    return CommandResult(handled=True, final_text=_unknown_command_text(text))


def _unknown_command_text(text: str) -> str:
    return f"未知命令: {text}\n输入 /help 查看可用命令。"


def _execute_skill_command(
    tokenized: TokenizedCommand,
    ctx: CommandContext,
) -> CommandResult:
    registry = ctx.plugin_manager.get_service("skill_registry")
    if registry is None:
        return CommandResult(
            handled=True,
            final_text="未注册 skill_registry（SkillsPlugin 未加载？）",
        )

    tokens = tokenized.tokens
    action = tokens[0].lower() if tokens else "list"
    if action == "list" and len(tokens) <= 1:
        skills = sorted(registry.all_skills(), key=lambda item: item.name)
        if not skills:
            return CommandResult(
                handled=True,
                final_text="当前 SkillsPlugin 未发现 Skill。",
            )
        lines = ["当前已装配的 Skill:"]
        for skill in skills:
            lines.append(f"- `{skill.name}`  {skill.description}")
        return CommandResult(handled=True, final_text="\n".join(lines))

    if action == "show" and len(tokens) == 2:
        skill = registry.resolve(tokens[1])
        if skill is None:
            return CommandResult(
                handled=True,
                final_text=f"未找到名为「{tokens[1]}」的 skill。",
            )
        lines = [
            f"Skill: {skill.name}",
            f"描述: {skill.description}",
        ]
        if skill.when_to_use:
            lines.append(f"适用场景: {skill.when_to_use}")
        if skill.docs:
            lines.append("文档:")
            for doc in skill.docs:
                lines.append(f"- {doc.name}: {doc.description}")
        else:
            lines.append("文档: 无")
        lines.append(f"位置: {skill.location}")
        return CommandResult(handled=True, final_text="\n".join(lines))

    return CommandResult(
        handled=True,
        final_text="用法错误。\n`/skill list`\n`/skill show <name>`",
    )


async def _execute_mcp_command(
    tokenized: TokenizedCommand,
    ctx: CommandContext,
) -> CommandResult:
    tokens = tokenized.tokens
    action = tokens[0].lower() if tokens else "list"
    if action != "list" or len(tokens) > 1:
        return CommandResult(handled=True, final_text="用法错误。\n`/mcp list`")

    config_snapshot = ctx.plugin_manager.get_service("mcp_config_snapshot")
    tool_names = ctx.plugin_manager.get_service("mcp_tool_names")
    if not callable(config_snapshot) or not callable(tool_names):
        return CommandResult(
            handled=True,
            final_text="未注册 MCP 查询服务（MCPPlugin 未加载？）",
        )

    lines: list[str] = []
    snapshot = config_snapshot()
    if snapshot.exists:
        if snapshot.error is not None:
            return CommandResult(
                handled=True,
                final_text=f"读取 MCP 配置失败: {snapshot.error}",
            )
        lines.append("mcp.json 中的服务器:")
        servers = snapshot.servers
        if isinstance(servers, dict) and servers:
            for name, sc in sorted(servers.items()):
                if not isinstance(sc, dict):
                    lines.append(f"- {name} (无效配置)")
                    continue
                transport = str(sc.get("transport", "http")).lower()
                value = sc.get("command", "") if transport == "stdio" else sc.get("url", "")
                lines.append(f"- {name}: {transport} {value}".rstrip())
        else:
            lines.append("- 无 servers 条目")
    else:
        lines.append(f"未找到 {snapshot.path}")

    try:
        names = await tool_names()
    except Exception as exc:  # pragma: no cover - exercised by tests via text only
        return CommandResult(handled=True, final_text=f"加载 MCP 工具失败: {exc}")

    lines.append("")
    lines.append("当前 MCPPlugin 暴露的工具名:")
    if names:
        for name in names:
            lines.append(f"- {name}")
    else:
        lines.append("- 无 MCP 工具")
    return CommandResult(handled=True, final_text="\n".join(lines))


def _execute_plugin_command(
    tokenized: TokenizedCommand,
    ctx: CommandContext,
) -> CommandResult:
    tokens = tokenized.tokens
    action = tokens[0].lower() if tokens else "list"
    if action != "list" or len(tokens) > 1:
        return CommandResult(handled=True, final_text="用法错误。\n`/plugin list`")
    plugins = list(ctx.plugin_manager.list_plugins())
    if not plugins:
        return CommandResult(handled=True, final_text="当前没有已加载插件。")
    lines = ["已加载插件:"]
    for name, version in plugins:
        lines.append(f"- {name} v{version}")
    return CommandResult(handled=True, final_text="\n".join(lines))


async def _execute_session_command(
    tokenized: TokenizedCommand,
    ctx: CommandContext,
) -> CommandResult:
    tokens = tokenized.tokens
    if not tokens:
        return CommandResult(
            handled=True,
            final_text="用法错误。\n`/session new`\n`/session save`\n`/session export --format json [--tail N]`",
        )
    action = tokens[0].lower()
    if action == "save" and len(tokens) == 1:
        flush = ctx.plugin_manager.get_service("session_flush")
        if not callable(flush):
            return CommandResult(handled=True, final_text="未注册 session_flush（SessionPlugin 未加载？）")
        msg = flush(
            ctx.conversation_history,
            session_key=ctx.session_key,
            owner_id=ctx.owner_id,
        )
        return CommandResult(handled=True, final_text=str(msg))

    if action == "new" and len(tokens) == 1:
        return await _execute_session_new(ctx)

    if action == "export":
        return _execute_session_export(tokens[1:], ctx.conversation_history)

    return CommandResult(
        handled=True,
        final_text="用法错误。\n`/session new`\n`/session save`\n`/session export --format json [--tail N]`",
    )


async def _execute_session_new(ctx: CommandContext) -> CommandResult:
    flush = ctx.plugin_manager.get_service("session_flush")
    flush_msg = ""
    if callable(flush):
        flush_msg = str(
            flush(
                ctx.conversation_history,
                session_key=ctx.session_key,
                owner_id=ctx.owner_id,
            )
        )

    paths = resolve_memory_paths()
    try:
        rel = await archive_last_session_to_markdown(
            ctx.session_key or "default",
            paths,
            ctx.conversation_history,
        )
    except Exception as exc:
        return CommandResult(
            handled=True,
            final_text=f"归档失败: {exc}\n未切换到新会话，可修正后重试 /session new。",
        )

    sync_now = ctx.plugin_manager.get_service("memory_sync_now")
    if callable(sync_now):
        await sync_now()
    begin_new = ctx.plugin_manager.get_service("session_begin_new")
    begin_msg = ""
    if callable(begin_new):
        begin_msg = str(begin_new(session_key=ctx.session_key, owner_id=ctx.owner_id))

    lines: list[str] = []
    if flush_msg:
        lines.append(flush_msg)
    if rel:
        lines.append(f"已归档会话记忆: {rel}（会同步进入长期记忆索引）")
    else:
        lines.append("无可归档内容（对话过短或无 user/assistant 内容）。仍将开始新会话。")
    if begin_msg:
        lines.append(begin_msg)
    return CommandResult(
        handled=True,
        final_text="\n".join(lines),
        updated_conversation_history=[],
    )


def _execute_session_export(tokens: list[str], history: list[Any]) -> CommandResult:
    fmt = "json"
    tail: int | None = None
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token == "--format":
            if idx + 1 >= len(tokens):
                return CommandResult(
                    handled=True,
                    final_text="用法错误。\n`/session export --format json [--tail N]`",
                )
            fmt = tokens[idx + 1].lower()
            idx += 2
            continue
        if token == "--tail":
            if idx + 1 >= len(tokens):
                return CommandResult(
                    handled=True,
                    final_text="用法错误。\n`/session export --format json [--tail N]`",
                )
            try:
                tail = int(tokens[idx + 1])
            except ValueError:
                return CommandResult(handled=True, final_text="`--tail` 需要正整数。")
            idx += 2
            continue
        return CommandResult(
            handled=True,
            final_text="用法错误。\n`/session export --format json [--tail N]`",
        )

    if fmt != "json":
        return CommandResult(handled=True, final_text="当前仅支持 `--format json`。")
    if tail is not None and tail <= 0:
        return CommandResult(handled=True, final_text="`--tail` 需要正整数。")
    selected = history[-tail:] if tail else history
    data = messages_to_export_dicts(selected)
    return CommandResult(
        handled=True,
        final_text=json.dumps(data, ensure_ascii=False, indent=2),
    )
