"""CLI-side HTTP client for talking to a LuckBot gateway bot."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from luckbot.domains.session.keys import normalize_session_name


@dataclass(slots=True)
class GatewayClientResult:
    final_text: str
    payload: dict[str, Any]


class GatewayClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_gateway_base_url() -> str:
    raw = (os.getenv("LUCKBOT_GATEWAY_URL") or "").strip()
    if raw:
        return raw.rstrip("/")

    host = (os.getenv("LUCKBOT_GATEWAY_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = int(os.getenv("LUCKBOT_GATEWAY_PORT", "8000"))
    return f"http://{host}:{port}"


def gateway_client_timeout() -> float:
    raw = (os.getenv("LUCKBOT_GATEWAY_CLIENT_TIMEOUT") or "300").strip()
    try:
        timeout = float(raw)
    except ValueError:
        return 300.0
    return timeout if timeout > 0 else 300.0


def send_gateway_turn(
    text: str,
    *,
    session_name: str | None = None,
    owner_id: str | None = None,
    trace_id: str | None = None,
) -> GatewayClientResult:
    return _post_gateway_json(
        "/gateway/cli/turn",
        {
            "text": text,
            "session_key": normalize_session_name(session_name),
            "owner_id": owner_id,
            "trace_id": trace_id,
        },
    )


def use_gateway_base_url(url: str) -> None:
    normalized = url.strip().rstrip("/")
    if not normalized:
        raise GatewayClientError("gateway URL 不能为空")
    os.environ["LUCKBOT_GATEWAY_URL"] = normalized


def gateway_healthcheck() -> dict[str, Any]:
    url = f"{resolve_gateway_base_url()}/healthz"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=gateway_client_timeout()) as response:
            data = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise GatewayClientError(f"无法连接 gateway: {exc}") from exc
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise GatewayClientError("gateway healthz 返回了无效 JSON") from exc


def _post_gateway_json(path: str, payload: dict[str, Any]) -> GatewayClientResult:
    url = f"{resolve_gateway_base_url()}{path}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers=_gateway_headers(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=gateway_client_timeout()) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_payload = _decode_error_payload(exc)
        message = error_payload.get("message") or error_payload.get("error") or str(exc)
        raise GatewayClientError(message, status_code=exc.code) from exc
    except urllib.error.URLError as exc:
        raise GatewayClientError(f"无法连接 gateway: {exc}") from exc

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise GatewayClientError("gateway 返回了无效 JSON") from exc
    return GatewayClientResult(final_text=str(parsed.get("final_text") or ""), payload=parsed)


def _gateway_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = (os.getenv("LUCKBOT_GATEWAY_CLIENT_KEY") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _decode_error_payload(exc: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        body = exc.read().decode("utf-8")
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return {}
    return {}
