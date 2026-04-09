"""RunTrace 数据模型与 RunTraceCollector：结构化采集单次 run 的全部可观测事件。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallEvent:
    tool_call_id: str
    name: str
    args_summary: str
    output_summary: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    success: bool = True


@dataclass
class StepEvent:
    index: int
    tool_call_names: list[str] = field(default_factory=list)
    prompt_hash: str = ""
    prompt_length: int = 0
    prompt_changed: bool = False
    prompt_diff: str = ""       # 本步相对上一步新增的 prompt 内容（增量）
    prompt_diff_chars: int = 0  # 增量字符数
    llm_content: str = ""
    usage_in: int = 0
    usage_out: int = 0
    tool_events: list[ToolCallEvent] = field(default_factory=list)


@dataclass
class RunTrace:
    start_time: float = 0.0
    end_time: float = 0.0
    initial_tools: list[str] = field(default_factory=list)
    initial_prompt_hash: str = ""
    steps: list[StepEvent] = field(default_factory=list)
    skills_loaded: list[str] = field(default_factory=list)
    docs_selected: dict[str, list[str]] = field(default_factory=dict)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _prompt_hash(prompt: str) -> str:
    return hashlib.md5(prompt.encode()).hexdigest()[:8]


def _summarize_args(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for k, v in args.items():
        sv = str(v)
        parts.append(f'{k}="{_truncate(sv, 60)}"')
    return ", ".join(parts)


class RunTraceCollector:
    """有状态采集器，在各 hook 回调中累积事件，最终产出 RunTrace。"""

    def __init__(self) -> None:
        self._trace = RunTrace()
        self._step_index = 0
        self._last_prompt_hash = ""
        self._last_prompt_content = ""
        self._pending_tools: dict[str, ToolCallEvent] = {}

    @property
    def trace(self) -> RunTrace:
        return self._trace

    def reset(self) -> None:
        self._trace = RunTrace()
        self._step_index = 0
        self._last_prompt_hash = ""
        self._last_prompt_content = ""
        self._pending_tools.clear()

    def on_before_run(self, tools: dict[str, Any], system_prompt: str) -> None:
        self._trace.start_time = time.time()
        self._trace.initial_tools = sorted(tools.keys())
        self._trace.initial_prompt_hash = _prompt_hash(system_prompt)
        # 用 base prompt 初始化，使 Step 1 的 diff 为"相对 base 新增的部分"
        self._last_prompt_hash = self._trace.initial_prompt_hash
        self._last_prompt_content = system_prompt

    def on_before_llm_call(self, system_prompt: str) -> None:
        self._step_index += 1
        ph = _prompt_hash(system_prompt)
        changed = ph != self._last_prompt_hash

        # 计算增量：若新 prompt 以上一步内容为前缀，只记录追加部分；否则记录全文
        if changed:
            if system_prompt.startswith(self._last_prompt_content):
                diff = system_prompt[len(self._last_prompt_content):]
            else:
                diff = system_prompt
        else:
            diff = ""

        step = StepEvent(
            index=self._step_index,
            prompt_hash=ph,
            prompt_length=len(system_prompt),
            prompt_changed=changed,
            prompt_diff=diff.strip("\n"),
            prompt_diff_chars=len(diff),
        )
        self._trace.steps.append(step)
        self._last_prompt_hash = ph
        self._last_prompt_content = system_prompt

    def on_after_llm_call(self, response: Any, usage: dict[str, Any] | None) -> None:
        if not self._trace.steps:
            return
        step = self._trace.steps[-1]

        tool_calls = getattr(response, "tool_calls", None) or []
        step.tool_call_names = [tc["name"] for tc in tool_calls]

        content = getattr(response, "content", None) or ""
        step.llm_content = content if isinstance(content, str) else str(content)

        if usage:
            step.usage_in = usage.get("input_tokens", 0)
            step.usage_out = usage.get("output_tokens", 0)

    def on_before_tool_call(
        self, name: str, args: dict[str, Any], tool_call_id: str,
    ) -> None:
        event = ToolCallEvent(
            tool_call_id=tool_call_id,
            name=name,
            args_summary=_summarize_args(args),
            start_time=time.time(),
        )
        self._pending_tools[tool_call_id] = event

    def on_after_tool_call(
        self, name: str, args: dict[str, Any], output: str, tool_call_id: str,
    ) -> None:
        event = self._pending_tools.pop(tool_call_id, None)
        if event is None:
            event = ToolCallEvent(
                tool_call_id=tool_call_id,
                name=name,
                args_summary=_summarize_args(args),
                start_time=time.time(),
            )
        event.end_time = time.time()
        event.output_summary = _truncate(output, 200)
        event.success = not output.startswith(("错误：", "执行工具", "工具异常"))

        if self._trace.steps:
            self._trace.steps[-1].tool_events.append(event)

        if name == "skill_load":
            skill_name = args.get("name", "")
            if skill_name and skill_name not in self._trace.skills_loaded:
                self._trace.skills_loaded.append(skill_name)
        elif name == "skill_select_docs":
            skill_name = args.get("skill_name", "")
            docs = args.get("docs", [])
            if skill_name and docs:
                self._trace.docs_selected[skill_name] = list(docs)

    def on_after_run(self) -> None:
        self._trace.end_time = time.time()
