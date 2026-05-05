"""Feishu adapter."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import deque
from typing import Any

from luckbot.adapters.gateway.feishu.client import FeishuApiClient, build_status_card
from luckbot.adapters.gateway.types import IncomingEnvelope, OutboundTarget, PlatformResponder, WebhookParseResult

logger = logging.getLogger(__name__)


class _FeishuResponder:
    def __init__(self, client: FeishuApiClient, incoming: IncomingEnvelope) -> None:
        self._client = client
        self._target = incoming.target
        self._card_message_id = ""

    async def send_progress(self, text: str) -> None:
        card = build_status_card("LuckBot 正在处理", text)
        if not self._card_message_id:
            try:
                self._card_message_id = await self._client.send_card(
                    receive_id=self._target.receive_id,
                    receive_id_type=self._target.receive_id_type,
                    card=card,
                )
            except Exception:
                logger.exception("feishu thinking card 发送失败")
            return
        try:
            await self._client.update_card(message_id=self._card_message_id, card=card)
        except Exception:
            logger.exception("feishu progress card 更新失败")
            self._card_message_id = ""

    async def send_final(self, text: str) -> None:
        card = build_status_card("LuckBot 已完成", text, done=True)
        if self._card_message_id:
            try:
                await self._client.update_card(message_id=self._card_message_id, card=card)
                return
            except Exception:
                logger.exception("feishu final card 更新失败，尝试降级发送新卡片")
                self._card_message_id = ""
        try:
            await self._client.send_card(
                receive_id=self._target.receive_id,
                receive_id_type=self._target.receive_id_type,
                card=card,
            )
        except Exception:
            logger.exception("feishu final card 发送失败，尝试降级发送文本")
            try:
                await self._client.send_text(
                    receive_id=self._target.receive_id,
                    receive_id_type=self._target.receive_id_type,
                    text=text or "任务已完成，但未返回正文。",
                )
            except Exception:
                logger.exception("feishu final text 发送失败")

    async def send_error(self, text: str) -> None:
        card = build_status_card("LuckBot 执行失败", text or "未知错误")
        if self._card_message_id:
            try:
                await self._client.update_card(message_id=self._card_message_id, card=card)
                return
            except Exception:
                logger.exception("feishu error card 更新失败，尝试降级发送新卡片")
                self._card_message_id = ""
        try:
            await self._client.send_card(
                receive_id=self._target.receive_id,
                receive_id_type=self._target.receive_id_type,
                card=card,
            )
        except Exception:
            logger.exception("feishu error card 发送失败，尝试降级发送文本")
            try:
                await self._client.send_text(
                    receive_id=self._target.receive_id,
                    receive_id_type=self._target.receive_id_type,
                    text=f"❌ {text or '未知错误'}",
                )
            except Exception:
                logger.exception("feishu error text 发送失败")


class FeishuAdapter:
    name = "feishu"

    def __init__(
        self,
        *,
        client: FeishuApiClient | None = None,
        verification_token: str | None = None,
        app_id: str | None = None,
    ) -> None:
        self._client = client or FeishuApiClient()
        self._verification_token = (
            verification_token
            if verification_token is not None
            else os.getenv("FEISHU_VERIFICATION_TOKEN", "")
        ).strip()
        self._app_id = (app_id if app_id is not None else os.getenv("FEISHU_APP_ID", "")).strip()
        self._seen_message_ids: deque[str] = deque(maxlen=500)
        self._seen_message_set: set[str] = set()

    def verify_request(self, headers: dict[str, str], body: bytes) -> bool:
        del headers
        if not self._verification_token and not self._app_id:
            return True
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return False
        header = payload.get("header")
        header_dict = header if isinstance(header, dict) else {}
        if self._verification_token:
            token = str(header_dict.get("token") or payload.get("token") or "")
            if token != self._verification_token:
                return False
        if self._app_id:
            app_id = str(header_dict.get("app_id") or payload.get("app_id") or "")
            if app_id and app_id != self._app_id:
                return False
        return True

    def parse_request(self, headers: dict[str, str], body: bytes) -> WebhookParseResult:
        del headers
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return WebhookParseResult()

        if payload.get("type") == "url_verification":
            challenge = str(payload.get("challenge") or "")
            return WebhookParseResult(ack_payload={"challenge": challenge})

        event = payload.get("event")
        if not isinstance(event, dict):
            return WebhookParseResult()
        message = event.get("message")
        sender = event.get("sender")
        if not isinstance(message, dict) or not isinstance(sender, dict):
            return WebhookParseResult()

        message_id = str(message.get("message_id") or "")
        if not message_id or self._seen(message_id):
            return WebhookParseResult()
        if str(message.get("message_type") or "") != "text":
            return WebhookParseResult()

        sender_id = sender.get("sender_id")
        if not isinstance(sender_id, dict):
            return WebhookParseResult()
        open_id = str(sender_id.get("open_id") or "")
        if not open_id:
            return WebhookParseResult()

        raw_content = str(message.get("content") or "")
        try:
            content_payload = json.loads(raw_content or "{}")
        except json.JSONDecodeError:
            return WebhookParseResult()
        text = str(content_payload.get("text") or "").strip()
        if not text:
            return WebhookParseResult()

        chat_id = str(message.get("chat_id") or open_id)
        raw_chat_type = str(message.get("chat_type") or "p2p")
        chat_type = "group" if raw_chat_type == "group" else "dm"

        raw_mentions = message.get("mentions")
        mentions = self._extract_mentions(raw_mentions)
        if chat_type == "group":
            if not self._has_mentions(raw_mentions):
                return WebhookParseResult()
            text = re.sub(r"@\S+", "", text).strip()
            if not text:
                return WebhookParseResult()

        incoming = IncomingEnvelope(
            platform="feishu",
            chat_type=chat_type,
            chat_id=chat_id,
            user_id=open_id,
            message_id=message_id,
            text=text,
            session_key=(
                f"feishu:group:{chat_id}:{open_id}"
                if chat_type == "group"
                else f"feishu:{open_id}"
            ),
            owner_id=f"feishu:user:{open_id}",
            target=OutboundTarget(
                receive_id=chat_id if chat_type == "group" else open_id,
                receive_id_type="chat_id" if chat_type == "group" else "open_id",
                source_message_id=message_id,
            ),
            mentions=mentions,
            trace_id=str(((payload.get("header") or {}) if isinstance(payload.get("header"), dict) else {}).get("event_id") or ""),
            raw_event=event,
        )
        return WebhookParseResult(ack_payload={}, incoming=incoming)

    async def create_responder(self, incoming: IncomingEnvelope) -> PlatformResponder:
        return _FeishuResponder(self._client, incoming)

    def _seen(self, message_id: str) -> bool:
        if message_id in self._seen_message_set:
            return True
        if len(self._seen_message_ids) == self._seen_message_ids.maxlen:
            old = self._seen_message_ids.popleft()
            self._seen_message_set.discard(old)
        self._seen_message_ids.append(message_id)
        self._seen_message_set.add(message_id)
        return False

    @staticmethod
    def _extract_mentions(raw_mentions: Any) -> list[str]:
        if not isinstance(raw_mentions, list):
            return []
        mentions: list[str] = []
        for item in raw_mentions:
            if not isinstance(item, dict):
                continue
            mention_id = item.get("id")
            if isinstance(mention_id, dict):
                open_id = str(mention_id.get("open_id") or "").strip()
                if open_id:
                    mentions.append(open_id)
        return mentions

    @staticmethod
    def _has_mentions(raw_mentions: Any) -> bool:
        return isinstance(raw_mentions, list) and len(raw_mentions) > 0
