"""OpenTelemetry 与结构化日志轻封装。"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from luckbot.core.observability.context import current_observability_context
from luckbot.core.observability.settings import load_observability_settings

try:
    from opentelemetry import metrics as otel_metrics
    from opentelemetry import trace as otel_trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
except Exception:  # pragma: no cover - 依赖缺失时退化为空实现
    otel_metrics = None
    otel_trace = None
    OTLPMetricExporter = None
    OTLPSpanExporter = None
    FastAPIInstrumentor = None
    MeterProvider = None
    PeriodicExportingMetricReader = None
    Resource = None
    TracerProvider = None
    BatchSpanProcessor = None

logger = logging.getLogger(__name__)

_INITIALIZED = False
_METER: Any = None
_COUNTERS: dict[str, Any] = {}
_HISTOGRAMS: dict[str, Any] = {}
_INSTRUMENTED_APPS: set[int] = set()


def _resource_attributes(component: str) -> dict[str, str]:
    settings = load_observability_settings()
    return {
        "service.name": settings.service_name,
        "service.version": settings.service_version,
        "deployment.environment": settings.environment,
        "luckbot.component": component,
    }


def _otlp_http_signal_endpoint(base: str, signal: str) -> str:
    """把 OTLP HTTP 根地址规范成具体 signal endpoint。"""
    raw = (base or "").strip().rstrip("/")
    if not raw:
        return ""

    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    wanted = f"/v1/{signal}"

    if path.endswith(wanted):
        normalized_path = path
    elif path in ("", "/"):
        normalized_path = wanted
    else:
        normalized_path = f"{path}{wanted}"

    return urlunsplit(
        (parts.scheme, parts.netloc, normalized_path, parts.query, parts.fragment)
    )


def init_observability(*, component: str, app: Any | None = None) -> None:
    global _INITIALIZED, _METER

    settings = load_observability_settings()
    if settings.otel_enabled and not settings.otlp_endpoint:
        logger.warning("可观测已启用，但未配置 LUCKBOT_OTEL_EXPORTER_OTLP_ENDPOINT")

    if not _INITIALIZED and settings.otel_enabled and otel_trace is not None and Resource is not None:
        resource = Resource.create(_resource_attributes(component))

        if TracerProvider is not None and OTLPSpanExporter is not None and BatchSpanProcessor is not None:
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=_otlp_http_signal_endpoint(
                            settings.otlp_endpoint, "traces"
                        ),
                        headers=settings.otlp_headers or None,
                    )
                )
            )
            otel_trace.set_tracer_provider(tracer_provider)

        if (
            MeterProvider is not None
            and OTLPMetricExporter is not None
            and PeriodicExportingMetricReader is not None
        ):
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=_otlp_http_signal_endpoint(
                        settings.otlp_endpoint, "metrics"
                    ),
                    headers=settings.otlp_headers or None,
                )
            )
            meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[metric_reader],
            )
            otel_metrics.set_meter_provider(meter_provider)
            _METER = otel_metrics.get_meter(settings.service_name)

        _INITIALIZED = True

    if (
        app is not None
        and settings.otel_enabled
        and settings.auto_instrument_fastapi
        and FastAPIInstrumentor is not None
        and id(app) not in _INSTRUMENTED_APPS
    ):
        FastAPIInstrumentor.instrument_app(app)
        _INSTRUMENTED_APPS.add(id(app))


def get_tracer(name: str = "luckbot") -> Any | None:
    if otel_trace is None:
        return None
    return otel_trace.get_tracer(name)


@contextmanager
def start_span(name: str, *, attributes: dict[str, Any] | None = None) -> Iterator[Any | None]:
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
        yield span


def add_event(name: str, *, attributes: dict[str, Any] | None = None) -> None:
    if otel_trace is None:
        return
    span = otel_trace.get_current_span()
    if span is None:
        return
    try:
        span.add_event(name, attributes=attributes or {})
    except Exception:
        logger.debug("span.add_event 失败", exc_info=True)


def record_exception(exc: Exception) -> None:
    if otel_trace is None:
        return
    span = otel_trace.get_current_span()
    if span is None:
        return
    try:
        span.record_exception(exc)
    except Exception:
        logger.debug("span.record_exception 失败", exc_info=True)


def _meter() -> Any | None:
    global _METER
    if _METER is not None:
        return _METER
    settings = load_observability_settings()
    if otel_metrics is None:
        return None
    _METER = otel_metrics.get_meter(settings.service_name)
    return _METER


def increment_counter(name: str, value: int = 1, *, attributes: dict[str, Any] | None = None) -> None:
    meter = _meter()
    if meter is None:
        return
    counter = _COUNTERS.get(name)
    if counter is None:
        counter = meter.create_counter(name)
        _COUNTERS[name] = counter
    counter.add(value, attributes=attributes or {})


def record_histogram(name: str, value: int | float, *, attributes: dict[str, Any] | None = None) -> None:
    meter = _meter()
    if meter is None:
        return
    histogram = _HISTOGRAMS.get(name)
    if histogram is None:
        histogram = meter.create_histogram(name)
        _HISTOGRAMS[name] = histogram
    histogram.record(value, attributes=attributes or {})


def log_event(
    logger_obj: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    ctx = current_observability_context()
    payload: dict[str, Any] = {"event": event}
    if ctx is not None:
        payload.update(ctx.as_metadata())
    payload.update({key: value for key, value in fields.items() if value is not None})
    logger_obj.log(level, json.dumps(payload, ensure_ascii=False, default=str))


__all__ = [
    "add_event",
    "get_tracer",
    "increment_counter",
    "init_observability",
    "log_event",
    "record_exception",
    "record_histogram",
    "start_span",
]
