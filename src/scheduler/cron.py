"""APScheduler 定时器：新加坡时间 08:30 触发晨报发送。对应 schema 第 16 节。

用法：
    python -m src.scheduler.cron
"""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler

from src.config import get_config
from src.observability.logger import get_logger
from src.scheduler.daily_digest_job import DailyDigest

logger = get_logger(__name__)


def run_daily_digest() -> None:
    job = DailyDigest(dry_run=False)
    job.run()


def main() -> int:
    config = get_config()
    hour, minute = config.app.digest_hh_mm()
    scheduler = BlockingScheduler(timezone=config.app.timezone)
    scheduler.add_job(
        run_daily_digest,
        trigger="cron",
        hour=hour,
        minute=minute,
        timezone=config.app.timezone,
    )
    logger.info(
        "调度器已启动：每天 %02d:%02d (%s) 运行晨报；授时来源=%s",
        hour,
        minute,
        config.app.timezone,
        config.env.time_source,
    )
    # 联网授时时，启动即打印当前校正后的时间，便于核对
    if config.env.time_source != "system":
        from zoneinfo import ZoneInfo

        from src.timesource import now_local

        logger.info("当前校正时间：%s", now_local(ZoneInfo(config.app.timezone)).isoformat())
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
