"""LuckBot runtime 与 gateway 之间的桥接层。"""

from __future__ import annotations

import os

from luckbot.plugins.builtin import discover_builtin_plugins
from luckbot.core.config import resolve_project_path
from luckbot.core.runtime import DEFAULT_MAX_STEPS, RuntimeProfile, RuntimeSpec, run_runtime
from luckbot.core.plugin.manager import PluginManager
from luckbot.domains.session.state import resolve_session
from luckbot.domains.session.transcript import load_transcript_messages
from luckbot.application.commands import CommandContext, execute_command
from luckbot.application.prompt import SYSTEM_PROMPT
from luckbot.adapters.gateway.types import GatewayRunResult, IncomingEnvelope


class LuckBotGatewayService:
    """把标准化 webhook 消息转成一次 LuckBot runtime 调用。"""

    def __init__(
        self,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self._system_prompt = system_prompt
        self._max_steps = max_steps

    async def run_turn(self, incoming: IncomingEnvelope) -> GatewayRunResult:
        conversation_history = _initial_conversation_history(incoming.session_key)
        pm = PluginManager()
        await pm.initialize(
            plugins=discover_builtin_plugins(),
            plugin_dirs=[resolve_project_path(".luckbot/plugins")],
        )
        try:
            command_result = await execute_command(
                incoming.text,
                CommandContext(
                    transport="gateway",
                    plugin_manager=pm,
                    conversation_history=conversation_history,
                    session_key=incoming.session_key,
                    owner_id=incoming.owner_id,
                ),
            )
            if command_result.handled:
                return GatewayRunResult(
                    final_text=command_result.final_text,
                    messages=list(command_result.updated_conversation_history or []),
                )

            result = await run_runtime(
                RuntimeSpec(
                    system_prompt=self._system_prompt,
                    max_steps=self._max_steps,
                    profile=RuntimeProfile(
                        enable_run_hooks=True,
                        normalize_remote_skill_directive=True,
                    ),
                    session_key=incoming.session_key,
                    owner_id=incoming.owner_id,
                    trace_id=incoming.trace_id,
                    platform=incoming.platform,
                    gateway_message_id=incoming.message_id,
                    gateway_trace_id=incoming.trace_id,
                    chat_id=incoming.chat_id,
                    user_id=incoming.user_id,
                    plugin_manager=pm,
                    user_input=incoming.text,
                )
            )
            return GatewayRunResult(final_text=result.final_text, messages=result.messages)
        finally:
            await pm.destroy()


def _session_persist_enabled() -> bool:
    return os.getenv("LUCKBOT_SESSION_PERSIST", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _initial_conversation_history(session_key: str) -> list[object]:
    if not _session_persist_enabled():
        return []
    meta = resolve_session(session_key)
    return list(load_transcript_messages(meta.session_id))
