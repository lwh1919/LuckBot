"""Skills domain."""

from .directive import parse_skill_activation_directive
from .prompt import build_skill_prompt
from .registry import SkillRegistry
from .state import SkillStateStore
from .types import ExecResult, SkillActivationRequest, SkillDoc, SkillInfo, SkillState
from .workspace import SkillWorkspace

__all__ = [
    "ExecResult",
    "SkillActivationRequest",
    "SkillDoc",
    "SkillInfo",
    "SkillRegistry",
    "SkillState",
    "SkillStateStore",
    "SkillWorkspace",
    "build_skill_prompt",
    "parse_skill_activation_directive",
]
