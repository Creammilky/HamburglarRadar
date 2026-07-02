"""本地模拟交互命令（无需 Hermes/飞书接入即可测试 Milestone 3）。

用法：
    python -m src.agent.cli "总结 https://arxiv.org/abs/2607.01084"
    python -m src.agent.cli "查一下 cyber range LLM agent"
    python -m src.agent.cli --send "今日论文"      # 实际推送到飞书
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from src.agent.router import Router
from src.config import get_config
from src.feishu.hermes_adapter import HermesEvent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="模拟飞书群 @ 命令")
    parser.add_argument("message", nargs="+", help="消息文本（会自动视为已 @ 机器人）")
    parser.add_argument("--send", action="store_true", help="实际发送回复到飞书")
    parser.add_argument("--chat", default=None, help="chat_id（默认取 allowed_chat_ids 首个）")
    parser.add_argument("--user", default=None, help="user_id（默认取 allowed_user_ids 首个）")
    parser.add_argument("--dm", action="store_true", help="按私聊处理（默认群聊）")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    cfg = get_config()
    chat_id = args.chat or (cfg.gateway.allowed_chat_ids[0] if cfg.gateway.allowed_chat_ids else "oc_local")
    user_id = args.user or (cfg.gateway.allowed_user_ids[0] if cfg.gateway.allowed_user_ids else "ou_local")

    event = HermesEvent(
        event_id=str(uuid.uuid4()),
        chat_id=chat_id,
        user_id=user_id,
        message_id=str(uuid.uuid4()),
        text=" ".join(args.message),
        is_group=not args.dm,
        is_mention=True,
        timestamp="",
        raw_json={},
    )

    router = Router()
    reply = router.handle(event) if args.send else router.route(event)

    if reply is None:
        print("[忽略] 未回复（权限/未 @）")
        return 0
    print("=== 回复文本 ===")
    print(reply.text or "(无纯文本)")
    print("\n=== 卡片 JSON ===")
    print(json.dumps(reply.card, ensure_ascii=False, indent=2))
    if args.send:
        print("\n[已发送到飞书]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
