"""SkillWorkspace：沙箱管理 — 增量暂存、只读保护、符号链接、环境变量注入。

工作空间布局：
  /tmp/luckbot_ws_{session}/
  ├── skills/<name>/       ← 技能目录（只读保护）
  │   ├── out/ → ../../out
  │   └── work/ → ../../work
  ├── out/                 ← $OUTPUT_DIR
  ├── work/                ← $WORK_DIR
  └── runs/                ← $RUN_DIR（每次 execute 一个子目录）
""" 

from __future__ import annotations

import glob as _glob
import hashlib
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

from .types import ExecResult

logger = logging.getLogger(__name__)


class SkillWorkspace:
    """管理隔离工作空间的创建、暂存、执行和清理。"""

    def __init__(self) -> None:
        self._root: str | None = None
        self._staged_digests: dict[str, str] = {}

    # -- 公共 API -------------------------------------------------------

    def execute(
        self,
        skill_name: str,
        skill_base_dir: str,
        command: str = "",
        *,
        script_content: str = "",
        cwd: str = "",
        env: dict[str, str] | None = None,
        output_files: list[str] | None = None,
        timeout: int = 30,
    ) -> ExecResult:
        """在隔离沙箱中执行命令。

        若 *script_content* 非空，自动将其写入 ``$WORK_DIR/_script.py``；
        此时若 *command* 为空，则默认执行 ``python3 $WORK_DIR/_script.py``。
        """
        root = self._ensure_root()
        self._stage_skill(skill_name, skill_base_dir)

        if script_content:
            work_dir = os.path.join(root, "work")
            script_path = os.path.join(work_dir, "_script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script_content)
            if not command:
                command = f"python3 {script_path}"

        if not command:
            return ExecResult(
                returncode=-1, stdout="", stderr="错误：command 和 script_content 至少需要提供一个。",
                timed_out=False, duration_ms=0, output_files={},
            )

        run_id = f"run_{uuid.uuid4().hex[:8]}"
        run_dir = os.path.join(root, "runs", run_id)
        os.makedirs(run_dir, exist_ok=True)

        skill_staged = os.path.join(root, "skills", skill_name)
        if cwd:
            work_cwd = os.path.join(skill_staged, cwd)
        else:
            work_cwd = skill_staged

        proc_env = self._build_env(skill_name, run_dir, env)

        start = time.monotonic_ns()
        timed_out = False
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=work_cwd,
                env=proc_env,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            returncode = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        duration_ms = int((time.monotonic_ns() - start) / 1_000_000)

        collected = self._collect_output_files(output_files, proc_env)

        return ExecResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=duration_ms,
            output_files=collected,
        )

    def cleanup(self) -> None:
        """销毁整个工作空间临时目录。"""
        if self._root is not None and os.path.isdir(self._root):
            shutil.rmtree(self._root, ignore_errors=True)
            logger.info("已清理工作空间: %s", self._root)
            self._root = None
            self._staged_digests.clear()

    # -- 内部 -----------------------------------------------------------

    def _ensure_root(self) -> str:
        if self._root is not None and os.path.isdir(self._root):
            return self._root
        self._root = tempfile.mkdtemp(prefix="luckbot_ws_")
        for sub in ("skills", "out", "work", "runs"):
            os.makedirs(os.path.join(self._root, sub), exist_ok=True)
        logger.info("创建工作空间: %s", self._root)
        return self._root

    def _stage_skill(self, skill_name: str, skill_base_dir: str) -> None:
        """增量暂存：目录哈希不变则跳过。"""
        digest = _compute_dir_digest(skill_base_dir)
        if self._staged_digests.get(skill_name) == digest:
            return

        root = self._ensure_root()
        dest = os.path.join(root, "skills", skill_name)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(skill_base_dir, dest, symlinks=False)

        self._link_workspace_dirs(dest)
        self._set_readonly(dest)
        self._staged_digests[skill_name] = digest
        logger.info("已暂存技能 %s → %s", skill_name, dest)

    def _link_workspace_dirs(self, skill_dest: str) -> None:
        """创建 out/ 和 work/ 符号链接指向工作空间共享目录。"""
        root = self._ensure_root()
        for dirname in ("out", "work"):
            link = os.path.join(skill_dest, dirname)
            if os.path.exists(link):
                if os.path.islink(link):
                    continue
                shutil.rmtree(link) if os.path.isdir(link) else os.remove(link)
            target = os.path.relpath(os.path.join(root, dirname), skill_dest)
            os.symlink(target, link)

    @staticmethod
    def _set_readonly(skill_dest: str) -> None:
        """将暂存的技能文件设为只读，符号链接除外。"""
        for root_dir, dirs, files in os.walk(skill_dest, followlinks=False):
            for name in files:
                fpath = os.path.join(root_dir, name)
                if os.path.islink(fpath):
                    continue
                try:
                    current = os.stat(fpath).st_mode
                    os.chmod(fpath, current & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
                except OSError:
                    pass
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root_dir, d))]

    def _build_env(
        self,
        skill_name: str,
        run_dir: str,
        extra: dict[str, str] | None,
    ) -> dict[str, str]:
        root = self._ensure_root()
        proc_env = dict(os.environ)
        proc_env.update({
            "WORKSPACE_DIR": root,
            "SKILLS_DIR": os.path.join(root, "skills"),
            "WORK_DIR": os.path.join(root, "work"),
            "OUTPUT_DIR": os.path.join(root, "out"),
            "RUN_DIR": run_dir,
            "SKILL_NAME": skill_name,
        })
        if extra is not None:
            proc_env.update(extra)
        return proc_env

    @staticmethod
    def _collect_output_files(
        patterns: list[str] | None,
        env: dict[str, str],
    ) -> dict[str, str]:
        """根据 glob 模式收集输出文件内容。"""
        if patterns is None or len(patterns) == 0:
            return {}

        collected: dict[str, str] = {}
        for pattern in patterns:
            expanded = pattern
            for var in ("$OUTPUT_DIR", "$WORK_DIR", "$WORKSPACE_DIR", "$RUN_DIR"):
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
                    logger.warning("无法读取输出文件: %s", fpath)
        return collected


def _compute_dir_digest(directory: str) -> str:
    """计算目录内容的 MD5 摘要（文件路径 + 大小 + mtime）。"""
    h = hashlib.md5()
    for root_dir, dirs, files in os.walk(directory, followlinks=False):
        dirs.sort()
        for fname in sorted(files):
            fpath = os.path.join(root_dir, fname)
            if os.path.islink(fpath):
                continue
            rel = os.path.relpath(fpath, directory)
            try:
                st = os.stat(fpath)
                h.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                pass
    return h.hexdigest()
