"""受控的飞书操作 wrapper。对应 schema 第 14、20 节。

安全要点：
- 只允许 allowlist 内的 (domain, action)，其余一律拒绝。
- 全程走官方 SDK / REST（无 shell、无 shell=True，不让 LLM 拼命令执行）。
- 高风险写操作可要求二次确认（require_confirmation）。
- 所有调用写审计日志，并对参数中的 secret 做屏蔽。

说明：schema 设想用 `lark` CLI 作为执行层；本实现改用官方 lark-oapi / Bitable REST，
从根上消除 shell 注入风险，等价且更安全。禁止的操作（删文档、删记录、拉全量通讯录、
大范围读群历史、自动邀请外部人、自动转发文件、任意 shell）不在 allowlist 内，天然被拒。
"""

from __future__ import annotations

import sqlite3
from typing import Literal, Optional

from pydantic import BaseModel

from src.config import AppConfig, get_config
from src.feishu.base_writer import FeishuBase
from src.observability.audit import AuditLogger
from src.observability.logger import get_logger

logger = get_logger(__name__)

# 第一版允许的操作（schema §14）
ALLOWED_OPERATIONS: dict[str, set[str]] = {
    "messenger": {"send_message", "reply_message"},
    "docs": {"create_doc", "append_doc", "read_doc"},
    "base": {"add_record", "search_records", "update_record"},
}


class LarkCliCommand(BaseModel):
    domain: Literal["messenger", "docs", "base", "sheets", "calendar", "tasks"]
    action: str
    args: dict = {}
    risk_level: Literal["low", "medium", "high"] = "low"
    require_confirmation: bool = False


class LarkCliResult(BaseModel):
    ok: bool
    data: dict = {}
    error: str = ""


class LarkCli:
    def __init__(
        self,
        config: Optional[AppConfig] = None,
        conn: Optional[sqlite3.Connection] = None,
    ):
        self.config = config or get_config()
        self.conn = conn
        self._base = FeishuBase(self.config)

    def _audit(self, cmd: LarkCliCommand, ok: bool, summary: str, user_id=None, chat_id=None):
        if self.conn is None:
            return
        try:
            AuditLogger(self.conn).record(
                event_type="lark_cli",
                risk_level=cmd.risk_level,
                user_id=user_id,
                chat_id=chat_id,
                tool_name=f"{cmd.domain}.{cmd.action}",
                tool_args=cmd.args,
                tool_result_summary=f"ok={ok}; {summary}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("审计写入失败：%s", exc)

    def is_allowed(self, cmd: LarkCliCommand) -> bool:
        return cmd.action in ALLOWED_OPERATIONS.get(cmd.domain, set())

    def run(
        self,
        cmd: LarkCliCommand,
        confirmed: bool = False,
        user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> LarkCliResult:
        # 1) allowlist 校验
        if not self.is_allowed(cmd):
            self._audit(cmd, False, "rejected: not in allowlist", user_id, chat_id)
            return LarkCliResult(ok=False, error=f"操作不被允许：{cmd.domain}.{cmd.action}")

        # 2) 二次确认校验
        if cmd.require_confirmation and not confirmed:
            return LarkCliResult(ok=False, error="该操作需要二次确认")

        # 3) 分发执行（无 shell）
        try:
            data = self._dispatch(cmd)
        except NotImplementedError as exc:
            self._audit(cmd, False, f"not implemented: {exc}", user_id, chat_id)
            return LarkCliResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            self._audit(cmd, False, f"error: {exc}", user_id, chat_id)
            return LarkCliResult(ok=False, error=str(exc))

        self._audit(cmd, True, str(data)[:200], user_id, chat_id)
        return LarkCliResult(ok=True, data=data)

    def _dispatch(self, cmd: LarkCliCommand) -> dict:
        app_token = self.config.env.feishu_base_app_token
        table_id = self.config.env.feishu_base_table_id
        a = cmd.args

        if cmd.domain == "base":
            if not (app_token and table_id):
                raise NotImplementedError("未配置 FEISHU_BASE_APP_TOKEN / FEISHU_BASE_TABLE_ID")
            if cmd.action == "search_records":
                items = self._base.search_records(
                    app_token, table_id, a["field"], a["value"]
                )
                return {"items": items}
            if cmd.action == "add_record":
                rid = self._base.add_record(app_token, table_id, a["fields"])
                return {"record_id": rid}
            if cmd.action == "update_record":
                rid = self._base.update_record(
                    app_token, table_id, a["record_id"], a["fields"]
                )
                return {"record_id": rid}

        raise NotImplementedError(f"未实现的操作：{cmd.domain}.{cmd.action}")
