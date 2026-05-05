from __future__ import annotations

import importlib

import pytest

gateway_control_module = importlib.import_module("luckbot.application.services.gateway_control")


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
