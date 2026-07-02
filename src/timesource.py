"""授时来源：可选联网校时，缓解服务器/本机系统时钟不准。

- system（默认）：直接用系统时钟。
- http：读取可信 HTTPS 响应头里的 Date（GMT/UTC），无需额外依赖。
- ntp：向 NTP 服务器请求（需 `pip install ntplib`）。

联网成功时计算与系统时钟的偏移量并缓存（默认 30 分钟刷新一次），
之后 now_utc() = 系统时钟 + 偏移量；联网失败自动回退系统时钟。

注意：定时触发由 APScheduler 依系统时钟发火，联网授时主要用于**校正晨报时间窗口**；
若要触发时刻也精确，请在服务器上启用 NTP 守护进程（chrony/systemd-timesyncd）。
"""

from __future__ import annotations

import threading
import time as _time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

from src.config import get_config
from src.observability.logger import get_logger

logger = get_logger(__name__)

_OFFSET_TTL_SECONDS = 1800  # 偏移量刷新周期
_lock = threading.Lock()
_offset_seconds = 0.0
_offset_epoch = 0.0  # 上次刷新的系统时间戳（0 表示未刷新）


def _fetch_network_utc() -> Optional[datetime]:
    env = get_config().env
    if env.time_source == "http":
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.head(env.time_http_url, follow_redirects=True)
                date_hdr = resp.headers.get("Date")
                if date_hdr:
                    return parsedate_to_datetime(date_hdr).astimezone(timezone.utc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HTTP 授时失败：%s", exc)
    elif env.time_source == "ntp":
        try:
            import ntplib  # 可选依赖

            resp = ntplib.NTPClient().request(env.ntp_server, version=3, timeout=5)
            return datetime.fromtimestamp(resp.tx_time, tz=timezone.utc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("NTP 授时失败（如缺 ntplib 请 pip install ntplib）：%s", exc)
    return None


def _refresh_offset() -> None:
    global _offset_seconds, _offset_epoch
    net = _fetch_network_utc()
    sys_utc = datetime.now(timezone.utc)
    if net is not None:
        _offset_seconds = (net - sys_utc).total_seconds()
        logger.info(
            "联网授时校正：offset=%.3fs（source=%s）", _offset_seconds, get_config().env.time_source
        )
    else:
        _offset_seconds = 0.0
        logger.warning("联网授时不可用，回退系统时钟")
    _offset_epoch = _time.monotonic()


def now_utc() -> datetime:
    """当前 UTC 时间（按配置的授时来源校正）。"""
    if get_config().env.time_source == "system":
        return datetime.now(timezone.utc)
    with _lock:
        if _offset_epoch == 0.0 or (_time.monotonic() - _offset_epoch) > _OFFSET_TTL_SECONDS:
            _refresh_offset()
        offset = _offset_seconds
    return datetime.now(timezone.utc) + timedelta(seconds=offset)


def now_local(tz) -> datetime:
    """当前指定时区时间。"""
    return now_utc().astimezone(tz)
