"""RemoteSkillPlugin：将单次入口指定的 skill 预加载到 SkillStateStore。"""

from __future__ import annotations

import logging

from luckbot.core.observability import log_event
from luckbot.core.plugin.base import LuckbotPlugin, PluginContext
from luckbot.core.plugin.hooks import BeforeRunInput
from luckbot.domains.skills.registry import SkillRegistry
from luckbot.domains.skills.state import SkillStateStore

logger = logging.getLogger(__name__)


class RemoteSkillPlugin(LuckbotPlugin):
    name = "luckbot/built-in-remote-skill"
    version = "0.1.0"
    dependencies = ["luckbot/built-in-skills"]

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None

    async def initialize(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        ctx.register_hook("before_run", self._before_run)

    async def _before_run(self, inp: BeforeRunInput) -> None:
        request = inp.remote_skill
        if request is None:
            return
        if self._ctx is None:
            raise RuntimeError("RemoteSkillPlugin 尚未初始化")

        registry = self._ctx.get_service("skill_registry")
        state = self._ctx.get_service("skill_state_store")
        if not isinstance(registry, SkillRegistry) or not isinstance(state, SkillStateStore):
            raise RuntimeError("Skill 服务未注册，无法预加载远程 skill")

        skill = registry.resolve(request.skill_name)
        if skill is None:
            available = sorted(s.name for s in registry.all_skills())
            raise ValueError(
                f"未找到名为「{request.skill_name}」的 skill。可用: {available}"
            )

        if request.docs:
            ok, invalid = registry.validate_docs(request.skill_name, request.docs)
            if not ok:
                raise ValueError(
                    f"远程 skill 文档不存在: {invalid}。可用文档: {sorted(registry.doc_names(request.skill_name))}"
                )

        state.preload(request)
        log_event(
            logger,
            logging.INFO,
            "remote_skill.preload",
            skill_name=request.skill_name,
            docs=request.docs or [],
            source=request.source,
        )


__all__ = ["RemoteSkillPlugin"]
