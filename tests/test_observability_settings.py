from __future__ import annotations

from luckbot.core.observability.settings import load_observability_settings


def test_langsmith_enabled_prefers_luckbot_flag(monkeypatch) -> None:
    monkeypatch.setenv("LUCKBOT_LANGSMITH_ENABLED", "0")
    monkeypatch.setenv("LANGSMITH_TRACING", "1")
    load_observability_settings.cache_clear()

    settings = load_observability_settings()

    assert settings.langsmith_enabled is False
    load_observability_settings.cache_clear()


def test_langsmith_enabled_keeps_legacy_fallback(monkeypatch) -> None:
    monkeypatch.delenv("LUCKBOT_LANGSMITH_ENABLED", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "1")
    load_observability_settings.cache_clear()

    settings = load_observability_settings()

    assert settings.langsmith_enabled is True
    load_observability_settings.cache_clear()
