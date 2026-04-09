"""一次性临时工作区：在独立目录中执行 shell 命令，结束后整目录删除。

与 SkillWorkspace 的区别：不暂存 skill，仅提供 work/out/runs 目录与环境变量。
注意：仍在同一用户权限下运行，不限制网络或访问 $HOME；仅隔离「默认 cwd」与临时文件。
"""

from __future__ import annotations

import glob as _glob
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SandboxRunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    output_files: dict[str, str] = field(default_factory=dict)


class EphemeralSandbox:
    """创建临时目录、执行命令、收集可选输出文件、删除整棵目录树。"""

    def run(
        self,
        command: str,
        *,
        timeout: int,
        env: dict[str, str] | None = None,
        output_files: list[str] | None = None,
    ) -> SandboxRunResult:
        root = tempfile.mkdtemp(prefix="luckbot_sbx_")
        work = os.path.join(root, "work")
        out = os.path.join(root, "out")
        runs = os.path.join(root, "runs")
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        run_dir = os.path.join(runs, run_id)
        os.makedirs(work, exist_ok=True)
        os.makedirs(out, exist_ok=True)
        os.makedirs(run_dir, exist_ok=True)

        proc_env = dict(os.environ)
        proc_env.update(
            {
                "SANDBOX_ROOT": root,
                "WORK_DIR": work,
                "OUTPUT_DIR": out,
                "RUN_DIR": run_dir,
            }
        )
        if env:
            proc_env.update(env)

        start = time.monotonic_ns()
        timed_out = False
        stdout = ""
        stderr = ""
        returncode = -1

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=work,
                env=proc_env,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            returncode = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        except Exception as exc:
            stderr = f"{stderr}\n{sandbox_internal_error(exc)}".strip()
            returncode = -1
        finally:
            duration_ms = int((time.monotonic_ns() - start) / 1_000_000)
            collected: dict[str, str] = {}
            try:
                collected = _collect_output_files(output_files, proc_env)
            except Exception as exc:
                logger.warning("收集沙箱输出文件失败: %s", exc)
            shutil.rmtree(root, ignore_errors=True)
            logger.debug("已删除沙箱目录: %s", root)

        return SandboxRunResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=duration_ms,
            output_files=collected,
        )


def sandbox_internal_error(exc: BaseException) -> str:
    return f"[沙箱内部错误] {exc}"


def _collect_output_files(
    patterns: list[str] | None,
    env: dict[str, str],
) -> dict[str, str]:
    if not patterns:
        return {}

    collected: dict[str, str] = {}
    for pattern in patterns:
        expanded = pattern
        for var in ("$OUTPUT_DIR", "$WORK_DIR", "$SANDBOX_ROOT", "$RUN_DIR"):
            key = var[1:]
            if key in env:
                expanded = expanded.replace(var, env[key])

        for fpath in _glob.glob(expanded, recursive=True):
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    collected[os.path.basename(fpath)] = f.read()
            except OSError:
                logger.warning("无法读取沙箱输出文件: %s", fpath)
    return collected
