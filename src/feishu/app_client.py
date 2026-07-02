"""飞书自建应用客户端：用 App ID/Secret 通过消息 API 发送/回复卡片。

用于 Milestone 3 群聊长连接交互的回复通道（比自定义机器人 webhook 更准，
可回复到具体消息/会话，且无关键词安全限制）。SDK 内部自动管理 tenant_access_token。
"""

from __future__ import annotations

import json
from typing import Optional

from src.config import AppConfig, get_config
from src.observability.logger import get_logger

logger = get_logger(__name__)


class FeishuAppClient:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self._client = None

    @property
    def enabled(self) -> bool:
        env = self.config.env
        return bool(env.feishu_app_id and env.feishu_app_secret)

    def _lark(self):
        if self._client is None:
            import lark_oapi as lark

            self._client = (
                lark.Client.builder()
                .app_id(self.config.env.feishu_app_id)
                .app_secret(self.config.env.feishu_app_secret)
                .log_level(lark.LogLevel.ERROR)
                .build()
            )
        return self._client

    def send_card(self, chat_id: str, card: dict) -> dict:
        """向 chat 发送 interactive 卡片。"""
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = self._lark().im.v1.message.create(req)
        if not resp.success():
            logger.error("飞书发送失败 code=%s msg=%s", resp.code, resp.msg)
            return {"ok": False, "code": resp.code, "msg": resp.msg}
        return {"ok": True, "message_id": getattr(resp.data, "message_id", None)}

    def reply_card(self, message_id: str, card: dict) -> dict:
        """回复到指定消息（保持在同一会话/话题）。"""
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = self._lark().im.v1.message.reply(req)
        if not resp.success():
            logger.error("飞书回复失败 code=%s msg=%s", resp.code, resp.msg)
            return {"ok": False, "code": resp.code, "msg": resp.msg}
        return {"ok": True, "message_id": getattr(resp.data, "message_id", None)}

    def add_reaction(self, message_id: str, emoji_type: str) -> Optional[str]:
        """给消息加表情回复，返回 reaction_id（失败返回 None，best-effort）。"""
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )

        try:
            req = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )
            resp = self._lark().im.v1.message_reaction.create(req)
            if not resp.success():
                logger.warning("加表情失败 emoji=%s code=%s msg=%s", emoji_type, resp.code, resp.msg)
                return None
            return getattr(resp.data, "reaction_id", None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("加表情异常 emoji=%s: %s", emoji_type, exc)
            return None

    def delete_reaction(self, message_id: str, reaction_id: str) -> bool:
        from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

        try:
            req = (
                DeleteMessageReactionRequest.builder()
                .message_id(message_id)
                .reaction_id(reaction_id)
                .build()
            )
            resp = self._lark().im.v1.message_reaction.delete(req)
            return resp.success()
        except Exception as exc:  # noqa: BLE001
            logger.warning("删表情异常: %s", exc)
            return False

    def list_chats(self) -> list[dict]:
        """列出机器人所在的群（用于验证接入 / 找 chat_id）。"""
        from lark_oapi.api.im.v1 import ListChatRequest

        req = ListChatRequest.builder().page_size(20).build()
        resp = self._lark().im.v1.chat.list(req)
        if not resp.success():
            logger.error("列出群失败 code=%s msg=%s", resp.code, resp.msg)
            return []
        items = getattr(resp.data, "items", None) or []
        return [{"chat_id": it.chat_id, "name": getattr(it, "name", "")} for it in items]
