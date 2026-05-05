from __future__ import annotations

import importlib

import pytest

llm_client_module = importlib.import_module("luckbot.core.llm.client")


def test_normalize_openai_base_url_adds_v1_for_root_path() -> None:
    assert (
        llm_client_module._normalize_openai_base_url("http://38.55.133.59:8080")
        == "http://38.55.133.59:8080/v1"
    )


def test_normalize_openai_base_url_keeps_explicit_v1() -> None:
    assert (
        llm_client_module._normalize_openai_base_url("http://38.55.133.59:8080/v1")
        == "http://38.55.133.59:8080/v1"
    )


def test_build_llm_forces_chat_completions_for_openai_compatible_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("LUCKBOT_MODEL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LUCKBOT_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("LUCKBOT_MODEL_NAME", "test-model")
    monkeypatch.setattr(llm_client_module, "ChatOpenAI", _FakeChatOpenAI)

    llm = llm_client_module.build_llm()

    assert isinstance(llm, _FakeChatOpenAI)
    assert captured["model"] == "test-model"
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://example.invalid/v1"
    assert captured["temperature"] == 0.2
    assert captured["use_responses_api"] is False
    assert captured["output_version"] == "v0"


def test_build_llm_rejects_missing_required_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUCKBOT_MODEL_BASE_URL", "")
    monkeypatch.setenv("LUCKBOT_MODEL_API_KEY", "")
    monkeypatch.setenv("LUCKBOT_MODEL_NAME", "")

    with pytest.raises(ValueError, match="LUCKBOT_MODEL_BASE_URL"):
        llm_client_module.build_llm()
