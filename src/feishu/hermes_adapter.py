"""Hermes Feishu 网关适配器。对应 schema 第 15 节。

- 把 Hermes 收到的原始消息解析为内部 HermesEvent。
- 提供群聊 @ 与 allowlist 判定 should_respond。
- 提供 send_text / send_card：优先走 Hermes 消息接口发送到飞书。

注意：Hermes 的具体发送 endpoint 依部署而定，这里给出可配置的默认实现；
若未部署 Hermes，可用 message_sender 的 Feishu webhook fallback。
"""

from __future__ import annotations

from typing import Optional

import httpx
from pydantic import BaseModel, Field

from src.config import AppConfig, get_config
from src.observability.logger import get_logger

logger = get_logger(__name__)


class HermesEvent(BaseModel):
    event_id: str
    chat_id: str
    user_id: str
    message_id: str
    text: str
    is_group: bool
    is_mention: bool
    timestamp: str
    # 飞书会话线程信息：话题内消息带 thread_id；引用回复带 root_id/parent_id
    thread_id: str = ""
    root_id: str = ""
    parent_id: str = ""
    raw_json: dict = Field(default_factory=dict)

    def conversation_id(self) -> str:
        """会话键：话题内回复共享同一会话（带历史）；群里直接 @ 则每条为新会话。

        - 话题(thread)：thread_id
        - 引用回复：root_id（指向线程根消息；根消息本身用其 message_id，二者一致 → 串起来）
        - 直接 @（无线程）：message_id（每条唯一 = 新会话）
        """
        return self.thread_id or self.root_id or self.message_id


def parse_hermes_event(raw: dict) -> HermesEvent:
    """把 Hermes 原始事件 dict 解析为 HermesEvent。字段容错。"""
    return HermesEvent(
        event_id=str(raw.get("event_id", raw.get("id", ""))),
        chat_id=str(raw.get("chat_id", "")),
        user_id=str(raw.get("user_id", raw.get("sender_id", ""))),
        message_id=str(raw.get("message_id", "")),
        text=str(raw.get("text", "")),
        is_group=bool(raw.get("is_group", False)),
        is_mention=bool(raw.get("is_mention", raw.get("mentioned", False))),
        timestamp=str(raw.get("timestamp", "")),
        thread_id=str(raw.get("thread_id", "") or ""),
        root_id=str(raw.get("root_id", "") or ""),
        parent_id=str(raw.get("parent_id", "") or ""),
        raw_json=raw,
    )


def parse_feishu_event(raw: dict) -> HermesEvent:
    """解析飞书 im.message.receive_v1 事件为 HermesEvent（best-effort）。"""
    import json as _json

    header = raw.get("header", {})
    event = raw.get("event", {})
    message = event.get("message", {})
    sender = event.get("sender", {}).get("sender_id", {})

    text = ""
    content = message.get("content")
    if content:
        try:
            text = _json.loads(content).get("text", "")
        except Exception:  # noqa: BLE001
            text = str(content)

    mentions = message.get("mentions", []) or []
    chat_type = message.get("chat_type", "")

    return HermesEvent(
        event_id=str(header.get("event_id", "")),
        chat_id=str(message.get("chat_id", "")),
        user_id=str(sender.get("open_id", sender.get("user_id", ""))),
        message_id=str(message.get("message_id", "")),
        text=text,
        is_group=(chat_type == "group"),
        is_mention=len(mentions) > 0,
        timestamp=str(header.get("create_time", "")),
        thread_id=str(message.get("thread_id", "") or ""),
        root_id=str(message.get("root_id", "") or ""),
        parent_id=str(message.get("parent_id", "") or ""),
        raw_json=raw,
    )


def should_respond(event: HermesEvent, config: Optional[AppConfig] = None) -> bool:
    """权限与 @ 判定。对应 schema 第 13.3 与 20 节。"""
    config = config or get_config()
    gw = config.gateway

    # 群聊必须 @ 机器人
    if event.is_group and gw.group_reply_requires_mention and not event.is_mention:
        return False
    # allowlist：非授权群不响应
    if gw.allowed_chat_ids and event.chat_id not in gw.allowed_chat_ids:
        return False
    # allowlist：非授权用户不响应
    if gw.allowed_user_ids and event.user_id not in gw.allowed_user_ids:
        return False
    return True


class HermesAdapter:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self.base_url = self.config.env.hermes_base_url.rstrip("/")
        self.agent_name = self.config.env.hermes_agent_name

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:  # noqa: BLE001
                return {"status": resp.status_code}

    def send_text(
        self, chat_id: str, text: str, reply_to: str | None = None
    ) -> dict:
        payload = {
            "agent": self.agent_name,
            "chat_id": chat_id,
            "msg_type": "text",
            "content": {"text": text},
        }
        if reply_to:
            payload["reply_to"] = reply_to
        return self._post("/api/messages/send", payload)

    def send_card(
        self, chat_id: str, card: dict, reply_to: str | None = None
    ) -> dict:
        payload = {
            "agent": self.agent_name,
            "chat_id": chat_id,
            "msg_type": "interactive",
            "card": card,
        }
        if reply_to:
            payload["reply_to"] = reply_to
        return self._post("/api/messages/send", payload)
