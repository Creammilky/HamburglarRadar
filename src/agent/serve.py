"""交互服务：接收 Hermes / 飞书事件，路由并回复。对应 Milestone 3。

端点：POST /hermes/events
- 飞书事件订阅的 challenge 校验会被自动回显。
- 支持飞书原生 im.message.receive_v1，也支持已规范化的 HermesEvent JSON。

Hermes 若以 websocket 模式接入，可由其 bridge 把消息 POST 到本端点；
或直接把本端点配置为飞书事件回调 URL。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.agent.router import Router
from src.feishu.hermes_adapter import parse_feishu_event, parse_hermes_event
from src.observability.logger import get_logger

logger = get_logger(__name__)


def _make_handler(router: Router):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._respond({"code": 400, "msg": "invalid json"}, 400)
                return

            # 飞书事件订阅 URL 校验
            if "challenge" in data:
                self._respond({"challenge": data["challenge"]})
                return

            try:
                if "header" in data or "event" in data:
                    event = parse_feishu_event(data)
                else:
                    event = parse_hermes_event(data)
                router.handle(event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("事件处理失败")
                self._respond({"code": 500, "msg": str(exc)}, 200)
                return
            self._respond({"code": 0})

        def log_message(self, *args):  # 静音默认访问日志
            pass

    return Handler


def run_server(host: str = "0.0.0.0", port: int = 9000) -> None:
    router = Router()
    server = HTTPServer((host, port), _make_handler(router))
    logger.info("交互服务已启动：POST http://%s:%d/hermes/events", host, port)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("交互服务停止")
