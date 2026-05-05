"""主 agent 薄适配层：委托统一 runtime core。"""

from __future__ import annotations

from typing import Any

from luckbot.domains.skills.types import RemoteSkillRequest
from luckbot.core.plugin.manager import PluginManager
from luckbot.core.runtime import (
    DEFAULT_MAX_STEPS,
    RuntimeProfile,
    RuntimeSpec,
    run_react_loop,
    run_runtime,
)


async def agent_loop(
    user_input: str,
    plugin_manager: PluginManager,
    *,
    system_prompt: str,
    conversation_history: list[Any] | None = None,
    session_key: str | None = None,
    owner_id: str | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    remote_skill: RemoteSkillRequest | None = None,
) -> tuple[str, list[Any]]:
    """主 agent 入口：复用外部 PluginManager，并委托统一 runtime。"""
    result = await run_runtime(
        RuntimeSpec(
            system_prompt=system_prompt,
            max_steps=max_steps,
            profile=RuntimeProfile(
                enable_run_hooks=True,
                normalize_remote_skill_directive=True,
            ),
            session_key=session_key,
            owner_id=owner_id,
            plugin_manager=plugin_manager,
            user_input=user_input,
            conversation_history=list(conversation_history or []),
            remote_skill=remote_skill,
        )
    )
    return result.final_text, result.messages


__all__ = ["DEFAULT_MAX_STEPS", "agent_loop", "run_react_loop"]
