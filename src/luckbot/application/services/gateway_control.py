"""Local process management for the LuckBot gateway."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from luckbot.application.services.gateway_client import gateway_healthcheck, resolve_gateway_base_url
from luckbot.domains.session.state import resolve_state_dir

_START_TIMEOUT_SECONDS = 8.0
_STOP_TIMEOUT_SECONDS = 5.0
_TAIL_LINE_LIMIT = 120


@dataclass(slots=True)
class GatewayProcessStatus:
    running: bool
    pid: int | None
    host: str
    port: int
    started_at: float | None
    channels: tuple[str, ...]
    pid_path: str
    state_path: str
    log_path: str
    detail: str = ""


def gateway_runtime_dir() -> Path:
    return resolve_state_dir() / "gateway"


def gateway_pid_path() -> Path:
    return gateway_runtime_dir() / "gateway.pid"


def gateway_state_path() -> Path:
    return gateway_runtime_dir() / "gateway.state.json"


def gateway_log_path() -> Path:
    return gateway_runtime_dir() / "gateway.log"


def gateway_host() -> str:
    return (os.getenv("LUCKBOT_GATEWAY_HOST") or "0.0.0.0").strip() or "0.0.0.0"


def gateway_port() -> int:
    return int(os.getenv("LUCKBOT_GATEWAY_PORT", "8000"))


def read_gateway_status() -> GatewayProcessStatus:
    runtime_dir = gateway_runtime_dir()
    pid_path = gateway_pid_path()
    state_path = gateway_state_path()
    log_path = gateway_log_path()

    pid = _read_pid(pid_path)
    state = _read_state(state_path)
    running = pid is not None and _pid_alive(pid)
    detail = ""
    if pid is None:
        detail = "gateway 未运行"
    elif not running:
        detail = "检测到陈旧 PID 文件"
    else:
        detail = "gateway 运行中"

    return GatewayProcessStatus(
        running=running,
        pid=pid,
        host=str(state.get("host") or gateway_host()),
        port=int(state.get("port") or gateway_port()),
        started_at=_coerce_optional_float(state.get("started_at")),
        channels=tuple(state.get("channels") or ("feishu", "cli_remote")),
        pid_path=str(pid_path),
        state_path=str(state_path),
        log_path=str(log_path),
        detail=detail,
    )


def start_gateway_process() -> GatewayProcessStatus:
    status = read_gateway_status()
    if status.running:
        return status

    runtime_dir = gateway_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_runtime_files()

    log_path = gateway_log_path()
    project_root = Path(__file__).resolve().parents[4]
    command = [sys.executable, str(project_root / "main.py"), "gateway", "serve"]

    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    finally:
        log_handle.close()

    gateway_pid_path().write_text(f"{process.pid}\n", encoding="utf-8")
    _write_state(
        {
            "pid": process.pid,
            "host": gateway_host(),
            "port": gateway_port(),
            "started_at": time.time(),
            "base_url": resolve_gateway_base_url(),
            "channels": ["feishu", "cli_remote"],
            "command": command,
            "log_path": str(log_path),
        }
    )

    deadline = time.time() + _START_TIMEOUT_SECONDS
    while time.time() < deadline:
        if process.poll() is not None:
            _cleanup_stale_runtime_files()
            raise RuntimeError("gateway 启动失败，请检查日志。")
        try:
            payload = gateway_healthcheck()
        except Exception:
            time.sleep(0.2)
            continue
        if payload.get("status") == "ok":
            return read_gateway_status()

    _terminate_pid(process.pid, force=True)
    _cleanup_stale_runtime_files()
    raise RuntimeError("gateway 启动超时，请检查日志。")


def stop_gateway_process() -> GatewayProcessStatus:
    status = read_gateway_status()
    if status.pid is None:
        _cleanup_stale_runtime_files()
        return read_gateway_status()

    _terminate_pid(status.pid, force=False)
    deadline = time.time() + _STOP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if not _pid_alive(status.pid):
            _cleanup_stale_runtime_files()
            return read_gateway_status()
        time.sleep(0.1)

    _terminate_pid(status.pid, force=True)
    _cleanup_stale_runtime_files()
    return read_gateway_status()


def read_gateway_logs(*, line_limit: int = _TAIL_LINE_LIMIT) -> tuple[Path, list[str]]:
    path = gateway_log_path()
    if not path.is_file():
        return path, []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return path, []
    return path, lines[-line_limit:]


def _read_pid(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_state(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(payload: dict[str, object]) -> None:
    gateway_state_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid(pid: int, *, force: bool) -> None:
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except OSError:
        return


def _cleanup_stale_runtime_files() -> None:
    for path in (gateway_pid_path(), gateway_state_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _coerce_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
