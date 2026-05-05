"""可观测配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class ObservabilitySettings:
    service_name: str
    service_version: str
    environment: str
    otel_enabled: bool
    otlp_endpoint: str
    otlp_headers: str
    auto_instrument_fastapi: bool
    langsmith_enabled: bool
    langsmith_project: str


@lru_cache(maxsize=1)
def load_observability_settings() -> ObservabilitySettings:
    otlp_endpoint = (os.getenv("LUCKBOT_OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    langsmith_project = (
        os.getenv("LUCKBOT_LANGSMITH_PROJECT")
        or os.getenv("LANGSMITH_PROJECT")
        or "luckbot"
    ).strip()
    return ObservabilitySettings(
        service_name=(os.getenv("LUCKBOT_SERVICE_NAME") or "luckbot").strip() or "luckbot",
        service_version=(os.getenv("LUCKBOT_SERVICE_VERSION") or "0.1.0").strip() or "0.1.0",
        environment=(os.getenv("LUCKBOT_ENV") or "dev").strip() or "dev",
        otel_enabled=_env_flag(
            "LUCKBOT_OBSERVABILITY_ENABLED",
            default=bool(otlp_endpoint),
        ),
        otlp_endpoint=otlp_endpoint,
        otlp_headers=(os.getenv("LUCKBOT_OTEL_EXPORTER_OTLP_HEADERS") or "").strip(),
        auto_instrument_fastapi=_env_flag(
            "LUCKBOT_OTEL_AUTO_INSTRUMENT_FASTAPI",
            default=True,
        ),
        langsmith_enabled=_env_flag(
            "LUCKBOT_LANGSMITH_ENABLED",
            # 兼容旧环境变量；LuckBot 当前主开关是 LUCKBOT_LANGSMITH_ENABLED。
            default=_env_flag("LANGSMITH_TRACING", False),
        ),
        langsmith_project=langsmith_project,
    )


__all__ = ["ObservabilitySettings", "load_observability_settings"]
