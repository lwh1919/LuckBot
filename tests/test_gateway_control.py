from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

gateway_control_module = importlib.import_module("luckbot.application.gateway.control")
cli_module = importlib.import_module("luckbot.entrypoints.cli")


class _FakePopen:
    def __init__(self, *_args, **_kwargs) -> None:
        self.pid = 43210

    def poll(self):
        return None


def test_gateway_control_start_and_stop_manage_runtime_files(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(gateway_control_module.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(gateway_control_module, "_pid_alive", lambda pid: pid == 43210)
    monkeypatch.setattr(gateway_control_module, "gateway_healthcheck", lambda: {"status": "ok"})

    terminated: list[tuple[int, bool]] = []

    def _fake_terminate(pid: int, *, force: bool) -> None:
        terminated.append((pid, force))
        monkeypatch.setattr(gateway_control_module, "_pid_alive", lambda _pid: False)

    monkeypatch.setattr(gateway_control_module, "_terminate_pid", _fake_terminate)

    started = gateway_control_module.start_gateway_process()
    assert started.running is True
    assert started.pid == 43210
    assert gateway_control_module.gateway_pid_path().is_file()
    assert gateway_control_module.gateway_state_path().is_file()

    stopped = gateway_control_module.stop_gateway_process()
    assert stopped.running is False
    assert terminated == [(43210, False)]
    assert not gateway_control_module.gateway_pid_path().exists()


def test_gateway_cli_without_url_starts_local_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _fake_chat_gateway() -> None:
        calls.append("chat")

    monkeypatch.setattr(cli_module, "_ensure_gateway_running", lambda: calls.append("start"))
    monkeypatch.setattr(cli_module, "async_chat_gateway", _fake_chat_gateway)

    cli_module._run_gateway_from_args(SimpleNamespace(target=None, follow=False))

    assert calls == ["start", "chat"]


def test_gateway_cli_with_url_skips_local_autostart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _fake_chat_gateway() -> None:
        calls.append("chat")

    def _fail_start() -> None:
        raise AssertionError("remote gateway should not start local gateway")

    monkeypatch.setattr(cli_module, "_ensure_gateway_running", _fail_start)
    monkeypatch.setattr(cli_module, "async_chat_gateway", _fake_chat_gateway)
    monkeypatch.setattr(cli_module, "use_gateway_base_url", lambda url: calls.append(f"url:{url}"))

    cli_module._run_gateway_from_args(
        SimpleNamespace(target="http://gateway.example:8000", follow=False)
    )

    assert calls == ["url:http://gateway.example:8000", "chat"]
