"""审计日志。所有工具调用/写操作都应记录。对应 schema 7.7 与第 20 节。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.observability.logger import get_logger, redact_secrets

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLogger:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(
        self,
        event_type: str,
        risk_level: str = "low",
        user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_args: Optional[dict] = None,
        tool_result_summary: Optional[str] = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        args_json = None
        if tool_args is not None:
            args_json = redact_secrets(json.dumps(tool_args, ensure_ascii=False))
        result = None
        if tool_result_summary is not None:
            result = redact_secrets(tool_result_summary)[:1000]
        self.conn.execute(
            """
            INSERT INTO audit_logs (
                event_id, user_id, chat_id, event_type, tool_name,
                tool_args_json, tool_result_summary, risk_level, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                user_id,
                chat_id,
                event_type,
                tool_name,
                args_json,
                result,
                risk_level,
                _now_iso(),
            ),
        )
        self.conn.commit()
        logger.info("[audit] %s tool=%s risk=%s", event_type, tool_name, risk_level)
        return event_id
