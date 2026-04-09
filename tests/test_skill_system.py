"""Skill 系统烟测：验证 Registry、Workspace、Plugin 基础功能。"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.built_in.skills_plugin import SkillsPlugin
from agent.built_in.skills_plugin.registry import SkillRegistry
from agent.built_in.skills_plugin.workspace import SkillWorkspace
from agent.built_in import discover_builtin_plugins
from agent.plugin.base import PluginContext
from agent.plugin.hooks import BeforeLLMCallInput


def test_registry():
    print("=== Registry 测试 ===")
    reg = SkillRegistry()
    changed = reg.refresh_if_changed()
    print(f"  refresh_if_changed: {changed}")

    skills = reg.all_skills()
    print(f"  发现 {len(skills)} 个 skill:")
    for s in skills:
        print(f"    - {s.name}: {s.description[:60]}")
        if s.docs:
            for d in s.docs:
                print(f"        doc: {d.name} ({d.description[:40]})")

    mc = reg.resolve("math-calc")
    assert mc is not None, "math-calc skill 应该存在"
    print(f"  resolve('math-calc'): OK, content={len(mc.content)} chars")
    assert "core" in mc.content.lower() or "数学" in mc.content

    not_changed = reg.refresh_if_changed()
    assert not not_changed, "指纹未变不应重新扫描"
    print(f"  二次 refresh_if_changed: {not_changed} (应为 False)")

    print("  PASS\n")


def test_workspace():
    print("=== Workspace 测试 ===")
    reg = SkillRegistry()
    reg.refresh_if_changed()
    ws = SkillWorkspace()

    mc = reg.resolve("math-calc")
    assert mc is not None

    result = ws.execute(
        skill_name="math-calc",
        skill_base_dir=mc.base_dir,
        command='python3 -c \'from core.calc import safe_eval; print(safe_eval("2+2"))\'',
        timeout=15,
    )
    print(f"  returncode: {result.returncode}")
    print(f"  timed_out: {result.timed_out}")
    print(f"  duration: {result.duration_ms}ms")
    print(f"  stdout 前 200 字符: {result.stdout[:200]}")
    if result.stderr:
        print(f"  stderr: {result.stderr[:200]}")

    assert result.returncode == 0, f"脚本执行应成功，实际 returncode={result.returncode}"
    assert "4" in result.stdout
    print("  PASS\n")

    ws.cleanup()


async def test_plugin():
    print("=== Plugin 集成测试 ===")
    plugin = SkillsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)

    tools = ctx.get_tools()
    expected = {"skill_load", "skill_select_docs", "skill_get_doc", "skill_run"}
    actual = set(tools.keys())
    print(f"  注册的工具: {sorted(actual)}")
    assert actual == expected, f"工具不匹配: 缺少 {expected - actual}, 多余 {actual - expected}"

    load_result = await tools["skill_load"].ainvoke({"name": "math-calc"})
    print(f"  skill_load('math-calc'): {load_result[:80]}")
    assert plugin._state.loaded.get("math-calc"), "state 应标记 math-calc 为已加载"
    assert "已加载" in load_result

    inp = BeforeLLMCallInput(
        tools={},
        system_prompt="你是助手。",
        messages=[],
    )
    llm_result = await plugin._before_llm_call(inp)
    assert llm_result is not None, "before_llm_call 应返回结果"
    assert "math-calc" in llm_result.system_prompt, "system prompt 应包含已加载 skill"
    assert "可用技能列表" in llm_result.system_prompt, "system prompt 应包含 L0 概览"
    print(f"  before_llm_call 注入后 prompt 长度: {len(llm_result.system_prompt)} chars")

    run_result = await tools["skill_run"].ainvoke({
        "skill": "math-calc",
        "command": 'python3 -c \'from core.calc import safe_eval; print(safe_eval("3*3"))\'',
        "timeout": 20,
    })
    assert "退出码: 0" in run_result, f"skill_run 应成功: {run_result[:200]}"
    assert "9" in run_result
    print(f"  skill_run: OK")

    await plugin.destroy(ctx)
    print("  PASS\n")


def test_discover():
    print("=== 自动发现测试 ===")
    plugins = discover_builtin_plugins()
    names = [p.name for p in plugins]
    print(f"  发现 {len(plugins)} 个内置插件: {names}")
    assert "luckbot/built-in-mcp" in names, "应发现 mcp_plugin"
    assert "luckbot/built-in-skills" in names, "应发现 skills_plugin"
    print("  PASS\n")


def main():
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    test_discover()
    test_registry()
    test_workspace()
    asyncio.run(test_plugin())
    print("========== ALL TESTS PASSED ==========")


if __name__ == "__main__":
    main()
