"""Runtime 输入、输出与 profile 类型。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator

from luckbot.domains.skills.types import SkillActivationRequest
from luckbot.core.observability import ObservabilityContext
from luckbot.core.plugin.base import LuckbotPlugin
from luckbot.core.plugin.manager import PluginManager

DEFAULT_MAX_STEPS = 25


@dataclass(slots=True)
class RuntimeProfile:
    """一次运行的能力与生命周期 profile。"""

    plugins: list[LuckbotPlugin] = field(default_factory=list)
    enable_run_hooks: bool = True


@dataclass(slots=True)
class RuntimeContext:
    """一次 runtime run 的输入、身份与运行态。"""

    system_prompt: str
    messages: list[Any]
    max_steps: int
    profile: RuntimeProfile = field(default_factory=RuntimeProfile)
    session_key: str | None = None
    owner_id: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    platform: str | None = None
    gateway_message_id: str | None = None
    gateway_trace_id: str | None = None
    chat_id: str | None = None
    user_id: str | None = None
    plugin_manager: PluginManager | None = None
    requested_skill: SkillActivationRequest | None = None
    current_tools: dict[str, Any] = field(default_factory=dict)
    current_prompt: str = ""
    final_text: str = ""
    error: Exception | None = None

    def prepare(self, tools: dict[str, Any]) -> None:
        self.current_tools = dict(tools)
        self.current_prompt = self.system_prompt
        self.messages = list(self.messages)
        self.final_text = ""
        self.error = None

    def to_observability_context(self) -> ObservabilityContext:
        return ObservabilityContext.new(
            trace_id=self.trace_id,
            run_id=self.run_id,
            platform=self.platform,
            gateway_message_id=self.gateway_message_id,
            gateway_trace_id=self.gateway_trace_id,
            chat_id=self.chat_id,
            user_id=self.user_id,
        )

    def sync_observability_context(self, obs_ctx: ObservabilityContext) -> None:
        self.trace_id = obs_ctx.trace_id
        self.run_id = obs_ctx.run_id

    def as_identity_attributes(self) -> dict[str, Any]:
        data = {
            "luckbot.session_key": self.session_key,
            "luckbot.owner_id": self.owner_id,
        }
        return {key: value for key, value in data.items() if value not in (None, "")}

    def as_identity_metadata(self) -> dict[str, Any]:
        data = {
            "session_key": self.session_key,
            "owner_id": self.owner_id,
        }
        return {key: value for key, value in data.items() if value not in (None, "")}

    def as_identity_tags(self) -> list[str]:
        tags: list[str] = []
        if self.session_key:
            tags.append("session")
        if self.owner_id:
            tags.append("owner")
        return tags

    def as_run_attrs(self, obs_ctx: ObservabilityContext) -> dict[str, Any]:
        return {
            **obs_ctx.as_attributes(),
            **self.as_identity_attributes(),
            "luckbot.max_steps": self.max_steps,
            "luckbot.enable_run_hooks": self.profile.enable_run_hooks,
        }

@dataclass(slots=True)
class RuntimeResult:
    """统一 runtime 输出。"""

    final_text: str
    messages: list[Any]


_RUNTIME_CONTEXT: ContextVar[RuntimeContext | None] = ContextVar(
    "luckbot_runtime_context",
    default=None,
)


def current_runtime_context() -> RuntimeContext | None:
    return _RUNTIME_CONTEXT.get()


def set_runtime_context(ctx: RuntimeContext) -> Token[RuntimeContext | None]:
    return _RUNTIME_CONTEXT.set(ctx)


def reset_runtime_context(token: Token[RuntimeContext | None]) -> None:
    _RUNTIME_CONTEXT.reset(token)


@contextmanager
def use_runtime_context(ctx: RuntimeContext) -> Iterator[RuntimeContext]:
    token = set_runtime_context(ctx)
    try:
        yield ctx
    finally:
        reset_runtime_context(token)
