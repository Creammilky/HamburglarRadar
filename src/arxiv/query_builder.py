"""arXiv search_query 构造。对应 schema 第 9.1 节。

规则：
- 分类用 (cat:X OR cat:Y ...) 组合。
- 提交时间范围用 submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]，时间必须是 UTC。
- 本地时间（Asia/Singapore）在调用前转换为 UTC。
"""

from __future__ import annotations

from datetime import datetime, timezone

ARXIV_TIME_FMT = "%Y%m%d%H%M"


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # 无时区信息时假定为 UTC，避免隐式本地时区
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_arxiv_time(dt: datetime) -> str:
    """将 datetime 转为 UTC 并格式化为 arXiv 的 YYYYMMDDHHMM。"""
    return _to_utc(dt).strftime(ARXIV_TIME_FMT)


def build_category_clause(categories: list[str]) -> str:
    if not categories:
        return ""
    cats = " OR ".join(f"cat:{c}" for c in categories)
    return f"({cats})"


def build_query(
    categories: list[str],
    window_start: datetime,
    window_end: datetime,
) -> str:
    """构造完整 search_query 字符串。"""
    if window_end < window_start:
        raise ValueError("window_end must be >= window_start")
    date_clause = (
        f"submittedDate:[{format_arxiv_time(window_start)} "
        f"TO {format_arxiv_time(window_end)}]"
    )
    cat_clause = build_category_clause(categories)
    if cat_clause:
        return f"{cat_clause} AND {date_clause}"
    return date_clause


def build_id_query(arxiv_ids: list[str]) -> dict:
    """按 id_list 查询单篇/多篇论文的请求参数。"""
    return {"id_list": ",".join(arxiv_ids)}
