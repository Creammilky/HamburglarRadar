"""权限判定。对应 schema 第 13.3 与第 20 节。

- 群聊必须 @ 机器人；否则忽略（不回复）。
- allowed_chat_ids 外的群：忽略（不回复）。
- allowed_user_ids 外的用户：回复“无权限”。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config import AppConfig, get_config
from src.feishu.hermes_adapter import HermesEvent


@dataclass
class PermissionDecision:
    should_respond: bool  # 是否需要回复（False=完全忽略）
    allowed: bool  # 是否有权限执行命令
    reason: str = ""


def evaluate(event: HermesEvent, config: Optional[AppConfig] = None) -> PermissionDecision:
    config = config or get_config()
    gw = config.gateway

    # 群聊必须 @
    if event.is_group and gw.group_reply_requires_mention and not event.is_mention:
        return PermissionDecision(should_respond=False, allowed=False, reason="群聊未 @ 机器人")

    # 非授权群：忽略
    if gw.allowed_chat_ids and event.chat_id not in gw.allowed_chat_ids:
        return PermissionDecision(should_respond=False, allowed=False, reason="非授权群")

    # 非授权用户：回复无权限
    if gw.allowed_user_ids and event.user_id not in gw.allowed_user_ids:
        return PermissionDecision(
            should_respond=True, allowed=False, reason="用户不在授权名单"
        )

    return PermissionDecision(should_respond=True, allowed=True)
