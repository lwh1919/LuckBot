"""sandbox_run：在一次性临时目录中执行 shell 命令（工作区隔离）。

默认工作目录为沙箱内的 work/，环境变量 SANDBOX_ROOT、WORK_DIR、OUTPUT_DIR、RUN_DIR。
执行结束后整个临时目录被删除。仍拦截「blocked」级命令（sudo、dd、rm -rf / 等）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from .ephemeral import EphemeralSandbox

from .safety import SafetyConfig, classify_command, describe_block_reason, truncate_output


def build_sandbox_run_tool(safety: SafetyConfig) -> Any:
    """构建沙箱执行工具。"""
    sandbox = EphemeralSandbox()

    @tool(
        description=(
            "在一次性临时目录中执行 shell 命令（工作区隔离）。"
            "默认工作目录为空的 work/，执行结束后整个临时目录会被删除，不污染项目树。"
            "适合复杂或脏脚本：pip install、大量临时文件、代码生成等。"
            "环境变量：SANDBOX_ROOT、WORK_DIR、OUTPUT_DIR、RUN_DIR。"
            "可将产物写入 $OUTPUT_DIR 或 $WORK_DIR；output_files 传入 glob 可回传匹配文件内容。"
            "仍会拦截主机级危险命令（sudo、dd、rm -rf / 等）。"
            "注意：非操作系统级沙箱，与 Agent 进程同一用户，网络未隔离。"
        ),
    )
    def sandbox_run(
        command: str,
        timeout: int = 0,
        output_files: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """在临时沙箱中执行 command。timeout 秒（0=默认 sandbox_timeout）。output_files 为 glob 列表。"""
        effective_timeout = timeout if timeout > 0 else safety.sandbox_timeout

        classification = classify_command(command, safety)
        if classification == "blocked":
            reason = describe_block_reason(command, safety)
            return (
                f"[已拦截] 沙箱仍禁止此类命令: {reason}\n"
                f"命令: {command}\n"
                f"提示: 若仅需在项目目录执行且命令安全，请用 bash。"
            )

        result = sandbox.run(
            command,
            timeout=effective_timeout,
            env=env,
            output_files=output_files,
        )

        lines: list[str] = []
        if result.timed_out:
            lines.append(
                f"[超时] 历时 {result.duration_ms} ms（上限 {effective_timeout} 秒）"
            )
        lines.append(f"退出码: {result.returncode}")
        lines.append(f"耗时（毫秒）: {result.duration_ms}")
        if result.stdout:
            lines.append(f"--- 标准输出 ---\n{result.stdout}")
        if result.stderr:
            lines.append(f"--- 标准错误 ---\n{result.stderr}")
        for fname, content in result.output_files.items():
            lines.append(f"--- 输出文件: {fname} ---\n{content}")

        output = "\n".join(lines) if lines else "（无输出）"
        return truncate_output(output, safety.sandbox_max_output)

    return sandbox_run
