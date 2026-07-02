"""飞书自建应用长连接网关。对应 Milestone 3（websocket 接入，本机无需公网）。

订阅 im.message.receive_v1，把事件转为 HermesEvent → Router 处理 → 用 App 回复到原消息。
每条消息在独立线程处理，避免耗时的 arXiv/LLM 调用阻塞长连接。
"""

from __future__ import annotations

import json
import threading

from src.agent.router import Router
from src.feishu.app_client import FeishuAppClient
from src.feishu.hermes_adapter import HermesEvent
from src.observability.logger import get_logger

logger = get_logger(__name__)

# 三阶段"活体"表情回复（已真机校验合法）：
EMOJI_WAIT = "OneSecond"   # 收到，稍等一下
EMOJI_WORKING = "Typing"   # 正在后台搜集/思考
EMOJI_DONE = "Fire"        # 🔥 工作接近完成

# 需要联网/LLM、耗时较长的命令
_HEAVY_INTENTS = {"daily_digest_now", "search_topic", "summarize_paper", "collision_check"}


def _event_to_hermes(data) -> HermesEvent:
    event = data.event
    message = event.message
    sender_id = getattr(event.sender, "sender_id", None)
    open_id = getattr(sender_id, "open_id", "") if sender_id else ""

    text = ""
    try:
        text = json.loads(message.content).get("text", "")
    except Exception:  # noqa: BLE001
        text = message.content or ""

    mentions = getattr(message, "mentions", None) or []
    return HermesEvent(
        event_id=getattr(data.header, "event_id", "") if getattr(data, "header", None) else "",
        chat_id=message.chat_id or "",
        user_id=open_id or "",
        message_id=message.message_id or "",
        text=text,
        is_group=(message.chat_type == "group"),
        is_mention=len(mentions) > 0,
        timestamp=str(getattr(message, "create_time", "")),
        raw_json={},
    )


def run_ws(config=None) -> None:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

    from src.config import get_config

    config = config or get_config()
    app_client = FeishuAppClient(config)
    if not app_client.enabled:
        raise RuntimeError("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，无法启动长连接")

    router = Router(config)

    def _process(data: P2ImMessageReceiveV1) -> None:
        from src.agent.command_parser import parse_command
        from src.agent.permissions import evaluate as evaluate_perm

        event = _event_to_hermes(data)
        logger.info("收到消息 chat=%s mention=%s: %s", event.chat_id, event.is_mention, event.text[:60])

        mid = event.message_id
        decision = evaluate_perm(event, config)
        # 只有会真正处理（有权限）的消息才加"活体"表情
        react = bool(mid and decision.should_respond and decision.allowed)
        stage_reaction: str | None = None  # 当前阶段表情的 reaction_id

        def _set_stage(emoji: str) -> None:
            nonlocal stage_reaction
            if not react:
                return
            if stage_reaction:
                app_client.delete_reaction(mid, stage_reaction)
            stage_reaction = app_client.add_reaction(mid, emoji)

        try:
            # 1) 收到 → 稍等
            _set_stage(EMOJI_WAIT)

            # 2) 耗时命令后台工作 → 正在输入
            intent = parse_command(event.text)
            if intent.intent in _HEAVY_INTENTS:
                _set_stage(EMOJI_WORKING)

            reply = router.route(event)

            # 3) 工作快结束 → 火
            _set_stage(EMOJI_DONE)

            if reply is None:
                return
            if mid:
                app_client.reply_card(mid, reply.card)
            else:
                app_client.send_card(event.chat_id, reply.card)
        except Exception:  # noqa: BLE001
            logger.exception("处理飞书消息失败")

    def on_message(data: P2ImMessageReceiveV1) -> None:
        # 异步处理，避免 arXiv/LLM 耗时阻塞长连接心跳
        threading.Thread(target=_process, args=(data,), daemon=True).start()

    builder = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
        on_message
    )
    # 机器人自身加/删表情会回推 reaction 事件；注册空处理器以消除 "processor not found" 噪声。
    for _name in (
        "register_p2_im_message_reaction_created_v1",
        "register_p2_im_message_reaction_deleted_v1",
    ):
        _reg = getattr(builder, _name, None)
        if _reg is not None:
            builder = _reg(lambda data: None)
    handler = builder.build()
    ws_client = lark.ws.Client(
        config.env.feishu_app_id,
        config.env.feishu_app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    logger.info("飞书长连接已启动，等待群内 @ 消息……（Ctrl+C 退出）")
    ws_client.start()
