"""Tools 插件烟测：验证 bash 安全拦截、文件操作、grep、ls、插件注册。"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.built_in.tools_plugin import ToolsPlugin
from agent.built_in.tools_plugin.safety import SafetyConfig, classify_command
from agent.built_in import discover_builtin_plugins
from agent.plugin.base import PluginContext


# ── safety 分类测试 ──────────────────────────────────────────────

def test_safety_classify():
    print("=== Safety 分类测试 ===")
    cfg = SafetyConfig()

    assert classify_command("ls -la", cfg) == "read"
    assert classify_command("cat /etc/hosts", cfg) == "read"
    assert classify_command("grep pattern file.py", cfg) == "read"
    assert classify_command("git status", cfg) == "read"
    assert classify_command("git log --oneline", cfg) == "read"
    assert classify_command("git push origin main", cfg) == "write"
    assert classify_command("python script.py", cfg) == "read"
    print("  read 命令分类: PASS")

    assert classify_command("mkdir new_dir", cfg) == "write"
    assert classify_command("cp file1 file2", cfg) == "write"
    assert classify_command("echo hello > file.txt", cfg) == "write"
    print("  write 命令分类: PASS")

    assert classify_command("sudo apt install", cfg) == "blocked"
    assert classify_command("dd if=/dev/zero", cfg) == "blocked"
    assert classify_command("shutdown -h now", cfg) == "blocked"
    assert classify_command("rm -rf /", cfg) == "blocked"
    assert classify_command("rm -rf ~", cfg) == "blocked"
    print("  blocked 命令分类: PASS")

    assert classify_command("cat file.txt | grep pattern", cfg) == "read"
    assert classify_command("ls | sort | head", cfg) == "read"
    print("  管道分类: PASS")

    print("  PASS\n")


# ── bash 工具测试 ────────────────────────────────────────────────

async def test_bash_tool():
    print("=== Bash 工具测试 ===")
    plugin = ToolsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()
    bash = tools["bash"]

    result = await bash.ainvoke({"command": "echo hello_luckbot"})
    assert "hello_luckbot" in result, f"echo 应正常输出: {result}"
    print(f"  echo: PASS")

    result = await bash.ainvoke({"command": "pwd"})
    assert "/" in result, f"pwd 应返回路径: {result}"
    print(f"  pwd: PASS")

    result = await bash.ainvoke({"command": "sudo rm -rf /"})
    assert "[已拦截]" in result, f"sudo 应被拦截: {result}"
    print(f"  blocked 拦截: PASS")

    result = await bash.ainvoke({"command": "nonexistent_cmd_xyz"})
    assert "[错误]" in result, f"不存在的命令应报错: {result}"
    print(f"  命令不存在: PASS")

    result = await bash.ainvoke({"command": "sleep 0.1", "timeout": 1})
    assert "[超时]" not in result
    print(f"  正常超时: PASS")

    print("  PASS\n")


# ── sandbox_run 工具测试 ─────────────────────────────────────────

async def test_sandbox_run_tool():
    print("=== sandbox_run 工具测试 ===")
    plugin = ToolsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()
    assert "sandbox_run" in tools, "应注册 sandbox_run"
    sb = tools["sandbox_run"]

    result = await sb.ainvoke({"command": "pwd"})
    assert "/work" in result.replace("\\", "/"), f"默认 cwd 应为 .../work: {result}"
    print("  cwd=work: PASS")

    result = await sb.ainvoke({"command": "echo hello_sbx > $OUTPUT_DIR/a.txt && cat $OUTPUT_DIR/a.txt"})
    assert "hello_sbx" in result
    assert "退出码: 0" in result
    print("  OUTPUT_DIR 写入: PASS")

    result = await sb.ainvoke({
        "command": "printf 'collected\\n' > $OUTPUT_DIR/out.txt",
        "output_files": ["$OUTPUT_DIR/out.txt"],
    })
    assert "退出码: 0" in result
    assert "collected" in result
    assert "--- 输出文件: out.txt ---" in result
    print("  output_files 收集: PASS")

    result = await sb.ainvoke({"command": "sudo true"})
    assert "[已拦截]" in result
    print("  blocked 拦截: PASS")

    print("  PASS\n")


# ── read 工具测试 ────────────────────────────────────────────────

async def test_read_tool():
    print("=== Read 工具测试 ===")
    plugin = ToolsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()
    read = tools["read"]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for i in range(1, 11):
            f.write(f"line {i}\n")
        tmp_path = f.name

    try:
        result = await read.ainvoke({"path": tmp_path})
        assert "line 1" in result and "line 10" in result
        print(f"  全文读取: PASS")

        result = await read.ainvoke({"path": tmp_path, "offset": 3, "limit": 2})
        assert "line 3" in result and "line 4" in result
        assert "line 5" not in result
        print(f"  offset+limit: PASS")

        result = await read.ainvoke({"path": "/nonexistent/file.txt"})
        assert "[错误]" in result
        print(f"  文件不存在: PASS")
    finally:
        os.unlink(tmp_path)

    print("  PASS\n")


# ── write + edit 工具测试 ────────────────────────────────────────

async def test_write_edit_tools():
    print("=== Write + Edit 工具测试 ===")
    plugin = ToolsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()

    tmp_dir = tempfile.mkdtemp()
    test_file = os.path.join(tmp_dir, "sub", "test.txt")

    try:
        result = await tools["write"].ainvoke({
            "path": test_file,
            "content": "hello world\nfoo bar\n",
        })
        assert "[OK]" in result and "新建" in result
        assert os.path.exists(test_file)
        print(f"  write (创建+子目录): PASS")

        result = await tools["edit"].ainvoke({
            "path": test_file,
            "old_string": "foo bar",
            "new_string": "baz qux",
        })
        assert "[OK]" in result
        with open(test_file) as f:
            content = f.read()
        assert "baz qux" in content and "foo bar" not in content
        print(f"  edit (替换): PASS")

        result = await tools["edit"].ainvoke({
            "path": test_file,
            "old_string": "not_in_file",
            "new_string": "something",
        })
        assert "[错误]" in result
        print(f"  edit (未找到): PASS")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("  PASS\n")


# ── ls 工具测试 ──────────────────────────────────────────────────

async def test_ls_tool():
    print("=== LS 工具测试 ===")
    plugin = ToolsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()
    ls = tools["ls"]

    result = await ls.ainvoke({"path": "."})
    assert "共" in result and "项" in result
    print(f"  当前目录: PASS")

    result = await ls.ainvoke({"path": "/nonexistent_dir_xyz"})
    assert "[错误]" in result
    print(f"  不存在目录: PASS")

    print("  PASS\n")


# ── grep 工具测试 ────────────────────────────────────────────────

async def test_grep_tool():
    print("=== Grep 工具测试 ===")
    plugin = ToolsPlugin()
    ctx = PluginContext()
    await plugin.initialize(ctx)
    tools = ctx.get_tools()
    grep = tools["grep"]

    tmp_dir = tempfile.mkdtemp()
    test_file = os.path.join(tmp_dir, "sample.py")
    with open(test_file, "w") as f:
        f.write("def hello():\n    return 'world'\n\ndef goodbye():\n    pass\n")

    try:
        result = await grep.ainvoke({"pattern": "def.*hello", "path": tmp_dir})
        assert "hello" in result
        print(f"  基础搜索: PASS")

        result = await grep.ainvoke({
            "pattern": "def", "path": tmp_dir, "glob_filter": "*.py",
        })
        assert "hello" in result or "goodbye" in result
        print(f"  glob 过滤: PASS")

        result = await grep.ainvoke({"pattern": "zzz_no_match", "path": tmp_dir})
        assert "无匹配" in result
        print(f"  无匹配: PASS")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("  PASS\n")


# ── 插件发现测试 ─────────────────────────────────────────────────

def test_discover_includes_tools():
    print("=== 自动发现含 ToolsPlugin 测试 ===")
    plugins = discover_builtin_plugins()
    names = [p.name for p in plugins]
    print(f"  发现 {len(plugins)} 个内置插件: {names}")
    assert "luckbot/built-in-tools" in names, "应发现 tools_plugin"
    assert "luckbot/built-in-skills" in names, "应发现 skills_plugin"
    print("  PASS\n")


# ── write 禁用测试 ───────────────────────────────────────────────

async def test_write_disabled():
    print("=== Write 禁用测试 ===")
    safety = SafetyConfig(allow_write_tools=False)
    plugin = ToolsPlugin(safety=safety)
    ctx = PluginContext()
    await plugin.initialize(ctx)

    from agent.plugin.hooks import BeforeToolCallInput
    try:
        await plugin._safety_hook(BeforeToolCallInput(name="write", args={}))
        assert False, "应抛出 RuntimeError"
    except RuntimeError as exc:
        assert "已拦截" in str(exc)
        print(f"  write 被 hook 拦截: PASS")

    print("  PASS\n")


# ── 主入口 ───────────────────────────────────────────────────────

def main():
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))

    test_safety_classify()
    test_discover_includes_tools()

    asyncio.run(test_bash_tool())
    asyncio.run(test_sandbox_run_tool())
    asyncio.run(test_read_tool())
    asyncio.run(test_write_edit_tools())
    asyncio.run(test_ls_tool())
    asyncio.run(test_grep_tool())
    asyncio.run(test_write_disabled())

    print("========== ALL TOOLS TESTS PASSED ==========")


if __name__ == "__main__":
    main()
