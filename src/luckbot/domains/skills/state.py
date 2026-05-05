"""SkillStateStore：Skill 工具写入、hook 读取的轻量状态层。"""

from __future__ import annotations

from copy import deepcopy

from .types import RemoteSkillRequest, SkillState


class SkillStateStore:
    """维护单次 run 的 skill 临时状态。"""

    def __init__(self) -> None:
        self._state = SkillState()

    def reset(self) -> None:
        self._state = SkillState()

    def snapshot(self) -> SkillState:
        return deepcopy(self._state)

    def mark_loaded(self, skill_name: str) -> None:
        self._state.loaded[skill_name] = True

    def select_docs(self, skill_name: str, docs: list[str]) -> None:
        self._state.selected_docs[skill_name] = list(docs)

    def preload(self, request: RemoteSkillRequest) -> None:
        self.mark_loaded(request.skill_name)
        if request.docs:
            self.select_docs(request.skill_name, request.docs)
