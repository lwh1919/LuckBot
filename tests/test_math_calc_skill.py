"""math-calc skill 端到端测试：模拟 LLM 编写脚本 + skill_run 执行。

验证模式三（库调用型）的完整流程：
  1. skill 发现与注册
  2. skill_load 加载 API 文档到上下文
  3. LLM 风格的脚本 import core.xxx 并执行
  4. 输出文件收集
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.built_in.skills_plugin import SkillsPlugin
from agent.built_in.skills_plugin.registry import SkillRegistry
from agent.plugin.base import PluginContext
from agent.plugin.hooks import BeforeLLMCallInput


def test_skill_discovery():
    print("=== math-calc 发现测试 ===")
    reg = SkillRegistry()
    reg.refresh_if_changed()

    skill = reg.resolve("math-calc")
    assert skill is not None, "math-calc skill 应被发现"
    assert "数学计算" in skill.description
    assert skill.when_to_use is not None, "should have when_to_use"
    print(f"  发现: {skill.name} - {skill.description[:60]}")
    print(f"  when_to_use: {skill.when_to_use}")
    print(f"  content length: {len(skill.content)} chars")
    print("  PASS\n")


async def test_skill_load_and_context():
    print("=== skill_load + 上下文注入测试 ===")
    plugin = SkillsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()

    result = await tools["skill_load"].ainvoke({"name": "math-calc"})
    assert "已加载" in result
    print(f"  skill_load: {result[:80]}")

    inp = BeforeLLMCallInput(tools={}, system_prompt="你是助手。", messages=[])
    llm_result = await plugin._before_llm_call(inp)
    assert llm_result is not None
    assert "core.calc" in llm_result.system_prompt
    assert "core.stats" in llm_result.system_prompt
    assert "core.solver" in llm_result.system_prompt
    assert "core.plotter" in llm_result.system_prompt
    assert "适用场景" in llm_result.system_prompt
    print(f"  上下文中包含全部 4 个模块 API: PASS")
    print(f"  L0 包含适用场景: PASS")
    print(f"  注入后 prompt 长度: {len(llm_result.system_prompt)} chars")

    await plugin.destroy(ctx)
    print("  PASS\n")


async def test_calc_module_via_skill_run():
    """模拟 LLM 编写 calc 脚本并通过 skill_run 执行。"""
    print("=== calc 模块 skill_run 测试 ===")
    plugin = SkillsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()

    script = textwrap.dedent("""
        from core.calc import safe_eval, derivative, integral, combination, convert_unit

        # 表达式求值
        result = safe_eval("sin(pi/4) * sqrt(2)")
        print(f"sin(pi/4)*sqrt(2) = {result}")
        assert abs(result - 1.0) < 1e-10, f"Expected ~1.0, got {result}"

        # 数值求导
        d = derivative(lambda x: x**3, 2.0)
        print(f"d/dx(x^3) at x=2 = {d}")
        assert abs(d - 12.0) < 1e-4

        # 数值积分
        area = integral(lambda x: x**2, 0, 1)
        print(f"∫x²dx from 0 to 1 = {area}")
        assert abs(area - 1/3) < 1e-8

        # 组合数
        c = combination(10, 3)
        print(f"C(10,3) = {c}")
        assert c == 120

        # 单位换算
        f = convert_unit(100, "C", "F")
        print(f"100°C = {f}°F")
        assert abs(f - 212.0) < 0.01

        print("ALL CALC TESTS PASSED")
    """).strip()

    result = await tools["skill_run"].ainvoke({
        "skill": "math-calc",
        "command": f'python3 -c "{_escape_for_shell(script)}"',
    })
    print(f"  output:\n{_indent(result)}")
    assert "退出码: 0" in result, f"脚本应成功执行: {result}"
    assert "ALL CALC TESTS PASSED" in result
    print("  PASS\n")

    await plugin.destroy(ctx)


async def test_stats_module_via_skill_run():
    """模拟 LLM 编写统计分析脚本。"""
    print("=== stats 模块 skill_run 测试 ===")
    plugin = SkillsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()

    script = textwrap.dedent("""
        from core.stats import describe, linear_regression, correlation, normal_cdf

        data = [23, 45, 12, 67, 34, 56, 78, 90, 11, 43]
        stats = describe(data)
        print(f"count={stats['count']}, mean={stats['mean']:.1f}, stdev={stats['stdev']:.1f}")
        assert stats["count"] == 10

        x = [1, 2, 3, 4, 5]
        y = [2, 4, 6, 8, 10]
        r = linear_regression(x, y)
        print(f"slope={r['slope']}, intercept={r['intercept']}, R²={r['r_squared']}")
        assert abs(r["slope"] - 2.0) < 1e-10
        assert abs(r["r_squared"] - 1.0) < 1e-10

        c = correlation(x, y)
        print(f"correlation = {c}")
        assert abs(c - 1.0) < 1e-10

        p = normal_cdf(0)
        print(f"P(X<=0) for N(0,1) = {p}")
        assert abs(p - 0.5) < 1e-10

        print("ALL STATS TESTS PASSED")
    """).strip()

    result = await tools["skill_run"].ainvoke({
        "skill": "math-calc",
        "command": f'python3 -c "{_escape_for_shell(script)}"',
    })
    print(f"  output:\n{_indent(result)}")
    assert "退出码: 0" in result
    assert "ALL STATS TESTS PASSED" in result
    print("  PASS\n")

    await plugin.destroy(ctx)


async def test_solver_module_via_skill_run():
    """模拟 LLM 编写方程求解脚本。"""
    print("=== solver 模块 skill_run 测试 ===")
    plugin = SkillsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()

    script = textwrap.dedent("""
        import math
        from core.solver import quadratic, solve_linear_system, find_root, minimize

        # 二次方程
        roots = quadratic(1, -5, 6)
        print(f"x²-5x+6=0 roots: {roots}")
        assert abs(roots[0] - 3.0) < 1e-10
        assert abs(roots[1] - 2.0) < 1e-10

        # 线性方程组
        x = solve_linear_system([[2, 1], [1, 3]], [5, 10])
        print(f"2x+y=5, x+3y=10 => x={x}")
        assert abs(x[0] - 1.0) < 1e-10
        assert abs(x[1] - 3.0) < 1e-10

        # 求根 cos(x) = x
        root = find_root(lambda x: math.cos(x) - x, 0, 1)
        print(f"cos(x)=x root: {root:.8f}")
        assert abs(root - 0.7390851332) < 1e-6

        # 最优化
        r = minimize(lambda x: (x - 3)**2, 0, 10)
        print(f"min of (x-3)² in [0,10]: x={r['x']:.6f}, f(x)={r['fx']:.8f}")
        assert abs(r["x"] - 3.0) < 1e-6

        print("ALL SOLVER TESTS PASSED")
    """).strip()

    result = await tools["skill_run"].ainvoke({
        "skill": "math-calc",
        "command": f'python3 -c "{_escape_for_shell(script)}"',
    })
    print(f"  output:\n{_indent(result)}")
    assert "退出码: 0" in result
    assert "ALL SOLVER TESTS PASSED" in result
    print("  PASS\n")

    await plugin.destroy(ctx)


async def test_plotter_ascii_via_skill_run():
    """模拟 LLM 编写绘图脚本（ASCII fallback）。"""
    print("=== plotter ASCII 模块 skill_run 测试 ===")
    plugin = SkillsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()

    script = textwrap.dedent("""
        import math
        from core.plotter import ascii_plot

        chart = ascii_plot(lambda x: math.sin(x), (-6.28, 6.28), width=40, height=12)
        print(chart)
        assert len(chart) > 50, "ASCII plot should have content"
        print("ASCII PLOT OK")
    """).strip()

    result = await tools["skill_run"].ainvoke({
        "skill": "math-calc",
        "command": f'python3 -c "{_escape_for_shell(script)}"',
    })
    print(f"  output:\n{_indent(result)}")
    assert "退出码: 0" in result
    assert "ASCII PLOT OK" in result
    print("  PASS\n")

    await plugin.destroy(ctx)


async def test_plotter_png_with_output_collection():
    """测试 PNG 绘图 + output_files 收集。"""
    print("=== plotter PNG + output_files 测试 ===")
    plugin = SkillsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()

    script = textwrap.dedent("""
        import math, os
        from core.plotter import plot_function, has_matplotlib

        if not has_matplotlib():
            print("matplotlib not available, using fallback")

        path = plot_function(
            lambda x: math.sin(x) * math.exp(-x/10),
            x_range=(-10, 10),
            title="damped sine",
            filename="damped_sine.png",
        )
        print(f"saved to: {path}")
        print(f"exists: {os.path.exists(path)}")
        print("PLOT FILE OK")
    """).strip()

    result = await tools["skill_run"].ainvoke({
        "skill": "math-calc",
        "command": f'python3 -c "{_escape_for_shell(script)}"',
    })
    print(f"  output:\n{_indent(result)}")
    assert "退出码: 0" in result
    assert "PLOT FILE OK" in result
    print("  PASS\n")

    await plugin.destroy(ctx)


# ── 辅助 ─────────────────────────────────────────────────────────

def _escape_for_shell(script: str) -> str:
    """转义脚本以便嵌入 shell 双引号。"""
    return script.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.split("\n"))


def main():
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))

    test_skill_discovery()
    asyncio.run(test_skill_load_and_context())
    asyncio.run(test_calc_module_via_skill_run())
    asyncio.run(test_stats_module_via_skill_run())
    asyncio.run(test_solver_module_via_skill_run())
    asyncio.run(test_plotter_ascii_via_skill_run())
    asyncio.run(test_plotter_png_with_output_collection())

    print("========== ALL MATH-CALC SKILL TESTS PASSED ==========")


if __name__ == "__main__":
    main()
