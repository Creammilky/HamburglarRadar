"""统一日志。禁止把 secret 写入日志。"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False

# 需要在日志中屏蔽的敏感环境变量键
_SECRET_ENV_KEYS = ("LLM_API_KEY", "FEISHU_WEBHOOK_SECRET")


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    _CONFIGURED = True


def redact_secrets(text: str) -> str:
    """从任意文本中屏蔽已知 secret 值。"""
    out = text
    for key in _SECRET_ENV_KEYS:
        val = os.getenv(key)
        if val:
            out = out.replace(val, "***REDACTED***")
    return out


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(name)
