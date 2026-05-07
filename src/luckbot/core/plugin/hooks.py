"""插件系统各 hook 的入参与返回值数据类。

六个 hook 覆盖 Agent 生命周期：
  before_run / after_run — 每次 run() 只触发一次
  before_llm_call / after_llm_call — 每轮 ReAct 迭代
  before_tool_call / after_tool_call — 每次工具调用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from luckbot.domains.skills.types import SkillActivationRequest
    from luckbot.core.runtime.types import RuntimeContext


# ---------------------------------------------------------------------------
# before_run / after_run
# ---------------------------------------------------------------------------

@dataclass
class BeforeRunInput:
    runtime_context: RuntimeContext
    tools: dict[str, Any]
    system_prompt: str
    """本次 run 的完整输入消息链（LangChain BaseMessage）。"""
    messages: list[Any] = field(default_factory=list)
    session_key: str | None = None
    owner_id: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    requested_skill: SkillActivationRequest | None = None


@dataclass
class BeforeRunResult:
    tools: dict[str, Any] | None = None
    system_prompt: str | None = None
    """若非 ``None``，则用其整体替换本次 run 的输入消息链。"""
    messages: list[Any] | None = None
    session_key: str | None = None
    owner_id: str | None = None


@dataclass
class AfterRunInput:
    runtime_context: RuntimeContext
    result: str
    messages: list[Any]
    session_key: str | None = None
    owner_id: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    error: Exception | None = None


# ---------------------------------------------------------------------------
# before_llm_call / after_llm_call
# ---------------------------------------------------------------------------

@dataclass
class BeforeLLMCallInput:
    runtime_context: RuntimeContext
    tools: dict[str, Any]
    system_prompt: str
    messages: list[Any]
    session_key: str | None = None
    owner_id: str | None = None
    trace_id: str | None = None
    run_id: str | None = None


@dataclass
class BeforeLLMCallResult:
    tools: dict[str, Any] | None = None
    system_prompt: str | None = None
    """若非 ``None``，替换当前 ReAct 轮次中的 ``messages``（如上下文压缩）。"""
    messages: list[Any] | None = None


@dataclass
class AfterLLMCallInput:
    runtime_context: RuntimeContext
    response: Any
    usage: dict[str, Any] | None = None
    trace_id: str | None = None
    run_id: str | None = None


# ---------------------------------------------------------------------------
# before_tool_call / after_tool_call
# ---------------------------------------------------------------------------

@dataclass
class BeforeToolCallInput:
    runtime_context: RuntimeContext
    name: str
    args: dict[str, Any]
    tool_call_id: str = ""
    trace_id: str | None = None
    run_id: str | None = None


@dataclass
class BeforeToolCallResult:
    """返回带修改后 *args* 的实例以改写入参；抛出异常可中止调用。"""
    args: dict[str, Any] | None = None


@dataclass
class AfterToolCallInput:
    runtime_context: RuntimeContext
    name: str
    args: dict[str, Any]
    output: str
    tool_call_id: str = ""
    trace_id: str | None = None
    run_id: str | None = None


@dataclass
class AfterToolCallResult:
    """返回带修改后 *output* 的实例以改写工具返回值。"""
    output: str | None = None
