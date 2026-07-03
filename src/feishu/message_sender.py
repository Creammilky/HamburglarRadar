"""统一消息发送层：Hermes 优先，Feishu 自定义机器人 webhook 作为 fallback。

对应 schema 第 15 节与第 19 节错误处理（发送失败保存到本地）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from src.config import REPO_ROOT, AppConfig, get_config
from src.feishu.hermes_adapter import HermesAdapter
from src.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SendResult:
    ok: bool
    channel: str
    detail: str = ""
    saved_path: Optional[str] = None


def _gen_sign(timestamp: str, secret: str) -> str:
    """Feishu 自定义机器人签名。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


class MessageSender:
    def __init__(self, config: Optional[AppConfig] = None, dry_run: bool = False):
        self.config = config or get_config()
        self.dry_run = dry_run
        self._hermes = HermesAdapter(self.config)
        self._app = None

    def _send_app(self, chat_id: str, card: dict) -> SendResult:
        """通过飞书自建应用消息 API 发送（推荐主通道）。"""
        from src.feishu.app_client import FeishuAppClient

        if self._app is None:
            self._app = FeishuAppClient(self.config)
        try:
            res = self._app.send_card(chat_id, card)
            if res.get("ok"):
                return SendResult(ok=True, channel="app", detail="sent")
            return SendResult(ok=False, channel="app", detail=str(res))
        except Exception as exc:  # noqa: BLE001
            return SendResult(ok=False, channel="app", detail=str(exc))

    @staticmethod
    def _inject_keyword(card: dict, keyword: str) -> dict:
        """若卡片内容未包含自定义机器人关键词，则注入一个 note 元素。"""
        if not keyword or keyword in json.dumps(card, ensure_ascii=False):
            return card
        elements = card.setdefault("elements", [])
        elements.append(
            {"tag": "note", "elements": [{"tag": "plain_text", "content": keyword}]}
        )
        return card

    # ---- webhook fallback ----
    def _send_webhook(self, card: dict, text: str) -> SendResult:
        env = self.config.env
        url = env.feishu_webhook_url
        if not url:
            return SendResult(ok=False, channel="webhook", detail="no webhook url")
        card = self._inject_keyword(card, env.feishu_webhook_keyword)
        body: dict = {"msg_type": "interactive", "card": card}
        if env.feishu_webhook_secret:
            ts = str(int(time.time()))
            body["timestamp"] = ts
            body["sign"] = _gen_sign(ts, env.feishu_webhook_secret)
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
            if data.get("StatusCode", data.get("code", 0)) not in (0, None):
                return SendResult(
                    ok=False, channel="webhook", detail=json.dumps(data, ensure_ascii=False)
                )
            return SendResult(ok=True, channel="webhook", detail="sent")
        except Exception as exc:  # noqa: BLE001
            return SendResult(ok=False, channel="webhook", detail=str(exc))

    # ---- hermes ----
    def _send_hermes(self, chat_id: str, card: dict) -> SendResult:
        try:
            self._hermes.send_card(chat_id, card)
            return SendResult(ok=True, channel="hermes", detail="sent")
        except Exception as exc:  # noqa: BLE001
            return SendResult(ok=False, channel="hermes", detail=str(exc))

    def _save_failed(self, chat_id: str, card: dict, text: str) -> str:
        out_dir = REPO_ROOT / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"failed_digest_{int(time.time())}.json"
        path.write_text(
            json.dumps(
                {"chat_id": chat_id, "card": card, "text": text},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(path)

    def send_message(self, chat_id: str, card: dict, text: str = "") -> SendResult:
        """通用消息发送（交互命令回复用），与晨报走同一路由与降级逻辑。"""
        return self.send_digest(chat_id, card, text)

    def send_digest(self, chat_id: str, card: dict, text: str) -> SendResult:
        """发送晨报卡片。dry_run 时不实际发送。失败时保存到本地。"""
        if self.dry_run:
            logger.info("[dry-run] 不实际发送飞书，chat_id=%s", chat_id or "(未配置)")
            return SendResult(ok=True, channel="dry_run", detail="not sent")

        env = self.config.env
        details: list[str] = []

        # 1) 优先：飞书自建应用（需 app 凭据 + chat_id）——最稳的主通道
        if env.feishu_app_id and env.feishu_app_secret and chat_id:
            app_res = self._send_app(chat_id, card)
            if app_res.ok:
                return app_res
            details.append(f"app={app_res.detail}")
            logger.warning("App 发送失败：%s，尝试其它通道", app_res.detail)

        # 2) Hermes（需要 chat_id）
        if self.config.feishu.sender == "hermes" and chat_id:
            hermes_res = self._send_hermes(chat_id, card)
            if hermes_res.ok:
                return hermes_res
            details.append(f"hermes={hermes_res.detail}")

        # 3) 自定义机器人 webhook 兜底
        wh = self._send_webhook(card, text)
        if wh.ok:
            return wh
        details.append(f"webhook={wh.detail}")

        # 都失败：保存到本地，不丢数据
        saved = self._save_failed(chat_id, card, text)
        logger.error("飞书发送失败，已保存到 %s", saved)
        return SendResult(
            ok=False, channel="failed", detail="; ".join(details), saved_path=saved
        )
