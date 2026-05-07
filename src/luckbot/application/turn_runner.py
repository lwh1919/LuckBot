"""Application use case for executing one channel turn."""

from __future__ import annotations

import os

from langchain_core.messages import HumanMessage

from luckbot.application.commands import CommandContext, execute_command
from luckbot.application.prompt import SYSTEM_PROMPT
from luckbot.application.turns import IncomingTurn, TurnResult
from luckbot.core.config import resolve_project_path
from luckbot.core.plugin.manager import PluginManager
from luckbot.core.runtime import DEFAULT_MAX_STEPS, RuntimeContext, RuntimeProfile, run_runtime
from luckbot.domains.session.state import resolve_session
from luckbot.domains.session.transcript import load_transcript_messages
from luckbot.domains.skills.directive import parse_skill_activation_directive
from luckbot.plugins.builtin import discover_builtin_plugins


class AgentTurnRunner:
    """Run slash commands or an agent turn for any inbound channel."""

    def __init__(
        self,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self._system_prompt = system_prompt
        self._max_steps = max_steps

    async def run_turn(self, turn: IncomingTurn) -> TurnResult:
        conversation_history = _initial_conversation_history(turn.session_key)
        pm = PluginManager()
        await pm.initialize(
            plugins=discover_builtin_plugins(),
            plugin_dirs=[resolve_project_path(".luckbot/plugins")],
        )
        try:
            command_result = await execute_command(
                turn.text,
                CommandContext(
                    channel=turn.channel,
                    transport=turn.transport,
                    plugin_manager=pm,
                    conversation_history=conversation_history,
                    session_key=turn.session_key,
                    owner_id=turn.owner_id,
                ),
            )
            if command_result.handled:
                return TurnResult(
                    final_text=command_result.final_text,
                    messages=(
                        list(command_result.updated_conversation_history)
                        if command_result.updated_conversation_history is not None
                        else conversation_history
                    ),
                    handled_as_command=True,
                )

            normalized_text, requested_skill = parse_skill_activation_directive(turn.text)
            messages = [*conversation_history, HumanMessage(content=normalized_text)]
            result = await run_runtime(
                RuntimeContext(
                    system_prompt=self._system_prompt,
                    max_steps=self._max_steps,
                    profile=RuntimeProfile(enable_run_hooks=True),
                    session_key=turn.session_key,
                    owner_id=turn.owner_id,
                    trace_id=turn.trace_id,
                    platform=turn.channel,
                    gateway_message_id=turn.message_id,
                    gateway_trace_id=turn.trace_id,
                    chat_id=turn.chat_id,
                    user_id=turn.user_id,
                    plugin_manager=pm,
                    messages=messages,
                    requested_skill=requested_skill,
                )
            )
            return TurnResult(final_text=result.final_text, messages=result.messages)
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
