from __future__ import annotations

from .types import CommandSpec

COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("/help", "显示可用命令"),
    CommandSpec("/skill list", "列出当前 SkillsPlugin 装配的 Skill"),
    CommandSpec("/skill show <name>", "显示当前 SkillsPlugin 中的 Skill 详情"),
    CommandSpec("/mcp list", "查看 MCPPlugin 配置与工具名"),
    CommandSpec("/plugin list", "查看已加载插件"),
    CommandSpec("/session new", "归档当前会话并开始新会话"),
    CommandSpec("/session save", "将当前会话同步到 JSONL transcript"),
    CommandSpec("/session export --format json [--tail N]", "导出当前会话消息"),
)


def render_help_text() -> str:
    lines = ["可用命令:"]
    for spec in COMMAND_SPECS:
        lines.append(f"- `{spec.usage}`  {spec.description}")
    return "\n".join(lines)
