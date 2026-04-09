"""内置 Skills 插件 v2：CQRS 架构 + 三层 Token 优化 + 沙箱执行。

4 个核心工具：
  skill_load        — L1 加载（CQRS 写入方，标记 state；L0 概览已通过 hook 始终注入）
  skill_select_docs — L2 选择文档（CQRS 写入方）
  skill_get_doc     — 即时返回单个文档内容（一次性，不持久注入）
  skill_run         — 沙箱执行命令

CQRS 读取方通过 before_llm_call hook 实现：
  每轮 LLM 调用前读取 state_delta，将已加载 skill 的 body 和已选文档注入 system prompt。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from agent.plugin.base import LuckbotPlugin, PluginContext
from agent.plugin.hooks import (
    BeforeLLMCallInput,
    BeforeLLMCallResult,
    BeforeRunInput,
    BeforeRunResult,
)
from .registry import SkillRegistry
from .types import SkillState
from .workspace import SkillWorkspace

logger = logging.getLogger(__name__)


class SkillsPlugin(LuckbotPlugin):
    """CQRS 驱动的 Skill 系统插件。"""

    name = "luckbot/built-in-skills"
    version = "0.2.0"

    def __init__(self) -> None:
        self._registry = SkillRegistry()
        self._workspace = SkillWorkspace()
        self._state = SkillState()

    async def initialize(self, ctx: PluginContext) -> None:
        self._registry.refresh_if_changed()

        ctx.register_tool("skill_load", self._build_skill_load_tool())
        ctx.register_tool("skill_select_docs", self._build_skill_select_docs_tool())
        ctx.register_tool("skill_get_doc", self._build_skill_get_doc_tool())
        ctx.register_tool("skill_run", self._build_skill_run_tool())

        ctx.register_hook("before_llm_call", self._before_llm_call)
        ctx.register_hook("before_run", self._before_run)

    async def destroy(self, ctx: PluginContext) -> None:
        self._workspace.cleanup()

    # ── hooks ──────────────────────────────────────────────────────

    async def _before_run(self, inp: BeforeRunInput) -> BeforeRunResult | None:
        self._registry.refresh_if_changed()
        self._state = SkillState()
        return None

    async def _before_llm_call(self, inp: BeforeLLMCallInput) -> BeforeLLMCallResult | None:
        """CQRS 读取方：将 L0 + L1 + L2 注入 system prompt。"""
        parts: list[str] = []

        # L0：始终注入 skill 概览（含 when_to_use 触发条件）
        skills = self._registry.all_skills()
        if skills:
            lines: list[str] = []
            for s in skills:
                entry = f"  - {s.name}: {s.description[:200]}"
                if s.when_to_use:
                    entry += f"\n    适用场景: {s.when_to_use}"
                lines.append(entry)
            listing = "\n".join(lines)
            parts.append(
                f"可用技能列表:\n{listing}\n"
                f"请使用 skill_load 加载技能，并按技能说明操作。"
            )

        # L1：注入已加载 skill 的 body
        for name in self._state.loaded:
            skill = self._registry.resolve(name)
            if skill is None:
                continue
            parts.append(f"--- 已加载技能: {skill.name} ---\n{skill.content}")
            if skill.docs:
                doc_list = "\n".join(
                    f"  - {d.name}: {d.description}" for d in skill.docs
                )
                parts.append(
                    f"可参考文档（用 skill_select_docs 加载）:\n{doc_list}"
                )

        # L2：注入已选文档内容
        for skill_name, doc_names in self._state.selected_docs.items():
            for doc_name in doc_names:
                content = self._registry.load_doc(skill_name, doc_name)
                if content is None:
                    continue
                parts.append(
                    f"--- 参考文档: {skill_name}/{doc_name} ---\n{content}"
                )

        if not parts:
            return None

        new_prompt = inp.system_prompt + "\n\n" + "\n\n".join(parts)
        return BeforeLLMCallResult(system_prompt=new_prompt)

    # ── 工具构造 ────────────────────────────────────────────────────

    def _build_skill_load_tool(self) -> Any:
        registry = self._registry
        state = self  # 通过 self 访问可变的 _state

        @tool(description="加载指定 skill，其指令将注入到后续对话上下文中。")
        def skill_load(name: str) -> str:
            """加载 skill 到上下文。加载后 skill 指令将在后续对话中可用。"""
            skill = registry.resolve(name)
            if skill is None:
                available = [s.name for s in registry.all_skills()]
                return f"未找到名为「{name}」的 skill。可用: {available}"
            state._state.loaded[name] = True
            msg = f"已加载技能「{name}」，其说明已注入后续对话上下文。"
            if skill.docs:
                doc_names = [d.name for d in skill.docs]
                msg += f"\n可参考文档: {doc_names}"
            return msg

        return skill_load

    def _build_skill_select_docs_tool(self) -> Any:
        registry = self._registry
        state = self

        @tool(
            description=(
                "选择一个或多个参考文档加载到上下文中。"
                "文档内容将在后续对话中可用。"
            )
        )
        def skill_select_docs(skill_name: str, docs: list[str]) -> str:
            """将指定 skill 的参考文档标记为已选，内容将注入后续对话上下文。"""
            skill = registry.resolve(skill_name)
            if skill is None:
                return f"未找到名为「{skill_name}」的 skill。"
            available_names = {d.name for d in skill.docs}
            invalid = [d for d in docs if d not in available_names]
            if invalid:
                return (
                    f"文档不存在: {invalid}。"
                    f"可用文档: {sorted(available_names)}"
                )
            state._state.selected_docs[skill_name] = list(docs)
            return f"已选择 {len(docs)} 个文档，内容将在后续对话中可用: {docs}"

        return skill_select_docs

    def _build_skill_get_doc_tool(self) -> Any:
        registry = self._registry

        @tool(description="获取指定 skill 的某个参考文档的完整内容。")
        def skill_get_doc(skill_name: str, doc_name: str) -> str:
            """即时返回文档内容，不走 CQRS 注入。"""
            content = registry.load_doc(skill_name, doc_name)
            if content is None:
                skill = registry.resolve(skill_name)
                if skill is None:
                    return f"未找到名为「{skill_name}」的 skill。"
                available = [d.name for d in skill.docs]
                return f"未找到文档「{doc_name}」。可用文档: {available}"
            return content

        return skill_get_doc

    def _build_skill_run_tool(self) -> Any:
        registry = self._registry
        workspace = self._workspace

        @tool(
            description=(
                "在隔离沙箱中执行命令。需要 skill 库的 import 路径时请用本工具。"
                "短脚本用 command='python3 -c \"...\"'；"
                "多行脚本传 script_content 参数（自动写入沙箱 $WORK_DIR/_script.py 并执行）。"
                "禁止用 write/bash 在项目目录创建临时脚本。"
                "注入环境变量: $WORKSPACE_DIR, $SKILLS_DIR, $WORK_DIR, $OUTPUT_DIR, $RUN_DIR, $SKILL_NAME。"
            )
        )
        def skill_run(
            skill: str,
            command: str = "",
            script_content: str = "",
            cwd: str = "",
            env: dict[str, str] | None = None,
            output_files: list[str] | None = None,
            timeout: int = 30,
        ) -> str:
            """在沙箱中执行命令，返回 stdout/stderr 和输出文件内容。

            多行脚本请传 script_content（自动落盘到沙箱内执行），
            command 和 script_content 至少提供一个。
            """
            info = registry.resolve(skill)
            if info is None:
                return f"未找到名为「{skill}」的 skill。"

            result = workspace.execute(
                skill_name=skill,
                skill_base_dir=info.base_dir,
                command=command,
                script_content=script_content,
                cwd=cwd,
                env=env,
                output_files=output_files,
                timeout=timeout,
            )

            lines: list[str] = []
            if result.timed_out:
                lines.append(f"[超时] 历时 {result.duration_ms} ms")
            lines.append(f"退出码: {result.returncode}")
            lines.append(f"耗时（毫秒）: {result.duration_ms}")
            if result.stdout:
                lines.append(f"--- 标准输出 ---\n{result.stdout}")
            if result.stderr:
                lines.append(f"--- 标准错误 ---\n{result.stderr}")
            for fname, content in result.output_files.items():
                lines.append(f"--- 输出文件: {fname} ---\n{content}")

            return "\n".join(lines)

        return skill_run
