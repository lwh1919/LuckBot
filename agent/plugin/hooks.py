"""插件系统各 hook 的入参与返回值数据类。

六个 hook 覆盖 Agent 生命周期：
  before_run / after_run — 每次 run() 只触发一次
  before_llm_call / after_llm_call — 每轮 ReAct 迭代
  before_tool_call / after_tool_call — 每次工具调用
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# before_run / after_run
# ---------------------------------------------------------------------------

@dataclass
class BeforeRunInput:
    tools: dict[str, Any]
    system_prompt: str


@dataclass
class BeforeRunResult:
    tools: dict[str, Any] | None = None
    system_prompt: str | None = None


@dataclass
class AfterRunInput:
    result: str
    messages: list[Any]


# ---------------------------------------------------------------------------
# before_llm_call / after_llm_call
# ---------------------------------------------------------------------------

@dataclass
class BeforeLLMCallInput:
    tools: dict[str, Any]
    system_prompt: str
    messages: list[Any]


@dataclass
class BeforeLLMCallResult:
    tools: dict[str, Any] | None = None
    system_prompt: str | None = None


@dataclass
class AfterLLMCallInput:
    response: Any
    usage: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# before_tool_call / after_tool_call
# ---------------------------------------------------------------------------

@dataclass
class BeforeToolCallInput:
    name: str
    args: dict[str, Any]


@dataclass
class BeforeToolCallResult:
    """返回带修改后 *args* 的实例以改写入参；抛出异常可中止调用。"""
    args: dict[str, Any] | None = None


@dataclass
class AfterToolCallInput:
    name: str
    args: dict[str, Any]
    output: str


@dataclass
class AfterToolCallResult:
    """返回带修改后 *output* 的实例以改写工具返回值。"""
    output: str | None = None
