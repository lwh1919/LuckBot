"""Feishu Open API client."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request


@dataclass(slots=True)
class _TokenCache:
    token: str = ""
    expires_at: float = 0.0


class FeishuApiClient:
    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._app_id = (app_id or os.getenv("FEISHU_APP_ID", "")).strip()
        self._app_secret = (app_secret or os.getenv("FEISHU_APP_SECRET", "")).strip()
        self._base_url = (
            (base_url or os.getenv("FEISHU_API_BASE_URL", "")).strip()
            or "https://open.feishu.cn"
        ).rstrip("/")
        self._token_cache = _TokenCache()
        self._token_lock = asyncio.Lock()

    async def send_text(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        text: str,
    ) -> str:
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        data = await self._authed_request(
            "POST",
            f"/open-apis/im/v1/messages?receive_id_type={parse.quote(receive_id_type)}",
            payload,
        )
        return str((data.get("data") or {}).get("message_id") or "")

    async def send_card(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        card: dict[str, Any],
    ) -> str:
        payload = {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        data = await self._authed_request(
            "POST",
            f"/open-apis/im/v1/messages?receive_id_type={parse.quote(receive_id_type)}",
            payload,
        )
        return str((data.get("data") or {}).get("message_id") or "")

    async def update_card(self, *, message_id: str, card: dict[str, Any]) -> None:
        await self._authed_request(
            "PATCH",
            f"/open-apis/im/v1/messages/{parse.quote(message_id)}",
            {"content": json.dumps(card, ensure_ascii=False)},
        )

    async def _authed_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        token = await self._get_tenant_access_token()
        return await asyncio.to_thread(
            self._request_json,
            method,
            f"{self._base_url}{path}",
            payload,
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )

    async def _get_tenant_access_token(self) -> str:
        if (
            self._token_cache.token
            and time.time() < self._token_cache.expires_at - 60
        ):
            return self._token_cache.token
        async with self._token_lock:
            if (
                self._token_cache.token
                and time.time() < self._token_cache.expires_at - 60
            ):
                return self._token_cache.token
            if not self._app_id or not self._app_secret:
                raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
            payload = await asyncio.to_thread(
                self._request_json,
                "POST",
                f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
                {
                    "app_id": self._app_id,
                    "app_secret": self._app_secret,
                },
                {"Content-Type": "application/json; charset=utf-8"},
            )
            token = str(payload.get("tenant_access_token") or "")
            expire = int(payload.get("expire") or 7200)
            if not token:
                raise RuntimeError(f"获取 Feishu tenant_access_token 失败: {payload}")
            self._token_cache = _TokenCache(
                token=token,
                expires_at=time.time() + expire,
            )
            return token

    @staticmethod
    def _request_json(
        method: str,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=body, method=method.upper())
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Feishu API 请求失败: {exc.code} {exc.reason} {detail}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(f"Feishu API 网络错误: {exc.reason}") from exc

        payload_data = json.loads(raw or "{}")
        if int(payload_data.get("code") or 0) != 0:
            raise RuntimeError(
                f"Feishu API 业务错误: {payload_data.get('code')} {payload_data.get('msg')}"
            )
        return payload_data


def build_status_card(title: str, body: str, *, done: bool = False) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "config": {"enable_forward": done},
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**{title}**\n\n{body}".strip(),
                }
            ]
        },
    }

