"""RunTrace 渲染器：将采集到的 trace 数据输出为可读的终端摘要。

支持 rich / plain / json 三种格式，由环境变量 LUCKBOT_TRACE_FORMAT 控制。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .collector import RunTrace


def render(trace: RunTrace) -> None:
    fmt = os.getenv("LUCKBOT_TRACE_FORMAT", "rich").lower()
    if fmt == "json":
        _render_json(trace)
    elif fmt == "plain":
        _render_plain(trace)
    else:
        _render_rich(trace)


def _duration_str(start: float, end: float) -> str:
    ms = (end - start) * 1000
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _render_rich(trace: RunTrace) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text

    console = Console(stderr=True)

    total_in = sum(s.usage_in for s in trace.steps)
    total_out = sum(s.usage_out for s in trace.steps)
    total_tools = sum(len(s.tool_events) for s in trace.steps)
    duration = _duration_str(trace.start_time, trace.end_time)

    header_lines = [
        f"Duration: {duration}  Steps: {len(trace.steps)}  Tool calls: {total_tools}",
    ]
    if total_in or total_out:
        header_lines.append(f"Tokens: {total_in:,} in / {total_out:,} out")
    if trace.skills_loaded:
        header_lines.append(f"Skills loaded: {', '.join(trace.skills_loaded)}")
    if trace.docs_selected:
        for skill, docs in trace.docs_selected.items():
            header_lines.append(f"Docs selected ({skill}): {', '.join(docs)}")
    header_lines.append(f"Tools: {', '.join(trace.initial_tools)}")

    panel = Panel(
        "\n".join(header_lines),
        title="LuckBot Run Trace",
        border_style="cyan",
        expand=False,
    )
    console.print()
    console.print(panel)

    for step in trace.steps:
        step_text = Text()
        step_text.append(f"\nStep {step.index}", style="bold")

        if step.tool_call_names:
            names = ", ".join(step.tool_call_names)
            step_text.append(f"\n  LLM -> {len(step.tool_call_names)} tool call(s): ")
            step_text.append(names, style="yellow")
        else:
            step_text.append("\n  LLM -> final answer (no tools)", style="green")

        prompt_info = f"({step.prompt_length:,} chars)"
        if step.prompt_changed:
            step_text.append(f"\n  Prompt: changed {prompt_info}", style="magenta")
        else:
            step_text.append(f"\n  Prompt: unchanged {prompt_info}")

        if step.usage_in or step.usage_out:
            step_text.append(
                f"\n  Tokens: {step.usage_in:,} in / {step.usage_out:,} out"
            )

        console.print(step_text)

        if step.prompt_changed and step.prompt_diff:
            console.print(Panel(
                Syntax(step.prompt_diff, "markdown", word_wrap=True),
                title=f"Prompt +{step.prompt_diff_chars:,} chars injected",
                border_style="magenta",
                expand=False,
                padding=(0, 1),
            ))

        for te in step.tool_events:
            elapsed = _duration_str(te.start_time, te.end_time)
            status = "OK" if te.success else "FAIL"
            style = "green" if te.success else "red bold"
            line = Text()
            args_display = te.args_summary[:80] if te.args_summary else ""
            line.append(f"  +-- {te.name}", style="cyan")
            if args_display:
                line.append(f"({args_display})")
            dots_len = max(2, 50 - len(te.name) - len(args_display))
            line.append(" " + "." * dots_len + " ")
            line.append(f"{elapsed} ", style="dim")
            line.append(status, style=style)
            console.print(line)

        if step.llm_content:
            console.print(Panel(
                step.llm_content.strip(),
                title=f"LLM Response (Step {step.index})",
                border_style="green" if not step.tool_call_names else "dim",
                expand=False,
                padding=(0, 1),
            ))

    console.print()


def _render_plain(trace: RunTrace) -> None:
    total_in = sum(s.usage_in for s in trace.steps)
    total_out = sum(s.usage_out for s in trace.steps)
    total_tools = sum(len(s.tool_events) for s in trace.steps)
    duration = _duration_str(trace.start_time, trace.end_time)

    lines = [
        "",
        f"=== LuckBot Run Trace ===",
        f"Duration: {duration}  Steps: {len(trace.steps)}  Tool calls: {total_tools}",
    ]
    if total_in or total_out:
        lines.append(f"Tokens: {total_in} in / {total_out} out")
    if trace.skills_loaded:
        lines.append(f"Skills loaded: {', '.join(trace.skills_loaded)}")
    lines.append(f"Tools: {', '.join(trace.initial_tools)}")
    lines.append("")

    for step in trace.steps:
        if step.tool_call_names:
            names = ", ".join(step.tool_call_names)
            lines.append(
                f"Step {step.index}: LLM -> {len(step.tool_call_names)} tool call(s): {names}"
            )
        else:
            lines.append(f"Step {step.index}: LLM -> final answer (no tools)")

        prompt_tag = "changed" if step.prompt_changed else "unchanged"
        lines.append(f"  Prompt: {prompt_tag} ({step.prompt_length} chars)")
        if step.prompt_changed and step.prompt_diff:
            lines.append(f"  [+{step.prompt_diff_chars} chars injected]")
            for pl in step.prompt_diff.splitlines():
                lines.append(f"    | {pl}")
        if step.usage_in or step.usage_out:
            lines.append(f"  Tokens: {step.usage_in} in / {step.usage_out} out")

        for te in step.tool_events:
            elapsed = _duration_str(te.start_time, te.end_time)
            status = "OK" if te.success else "FAIL"
            lines.append(f"  +-- {te.name}({te.args_summary[:80]}) {elapsed} {status}")

        if step.llm_content:
            lines.append(f"  [LLM Response]")
            for rl in step.llm_content.strip().splitlines():
                lines.append(f"    {rl}")
        lines.append("")

    import sys
    sys.stderr.write("\n".join(lines) + "\n")


def _render_json(trace: RunTrace) -> None:
    import sys
    sys.stderr.write(json.dumps(asdict(trace), ensure_ascii=False, indent=2) + "\n")
