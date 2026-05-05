from __future__ import annotations

from luckbot.core.observability.telemetry import _otlp_http_signal_endpoint


def test_otlp_http_signal_endpoint_appends_signal_path_for_root() -> None:
    assert (
        _otlp_http_signal_endpoint("http://localhost:4318", "traces")
        == "http://localhost:4318/v1/traces"
    )
    assert (
        _otlp_http_signal_endpoint("http://localhost:4318", "metrics")
        == "http://localhost:4318/v1/metrics"
    )


def test_otlp_http_signal_endpoint_keeps_explicit_signal_path() -> None:
    assert (
        _otlp_http_signal_endpoint("http://localhost:4318/v1/traces", "traces")
        == "http://localhost:4318/v1/traces"
    )


def test_otlp_http_signal_endpoint_appends_under_custom_prefix() -> None:
    assert (
        _otlp_http_signal_endpoint("http://localhost:4318/otlp", "metrics")
        == "http://localhost:4318/otlp/v1/metrics"
    )
