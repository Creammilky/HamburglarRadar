"""arXiv API 客户端。对应 schema 第 9 节。

- 使用 https://export.arxiv.org/api/query
- sortBy=submittedDate, sortOrder=descending
- 连续请求间隔 ARXIV_REQUEST_DELAY_SECONDS
- 失败最多重试 3 次，指数退避
- 返回归一化的 ArxivPaper 列表
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import feedparser
import httpx

from src.arxiv.normalizer import normalize_entry
from src.arxiv.query_builder import build_query
from src.config import get_config
from src.models import ArxivPaper
from src.observability.logger import get_logger

logger = get_logger(__name__)


class ArxivClientError(RuntimeError):
    pass


class ArxivClient:
    def __init__(
        self,
        api_base: Optional[str] = None,
        request_delay_seconds: Optional[float] = None,
        max_results_per_profile: Optional[int] = None,
        timeout_seconds: float = 30.0,
    ):
        cfg = get_config().env
        self.api_base = api_base or cfg.arxiv_api_base
        self.request_delay_seconds = (
            request_delay_seconds
            if request_delay_seconds is not None
            else cfg.arxiv_request_delay_seconds
        )
        self.max_results_per_profile = (
            max_results_per_profile or cfg.arxiv_max_results_per_profile
        )
        self.timeout_seconds = timeout_seconds
        self._last_request_ts = 0.0

    def _respect_delay(self) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < self.request_delay_seconds:
            time.sleep(self.request_delay_seconds - elapsed)

    def _raw_request(self, params: dict) -> str:
        """执行带重试的 HTTP GET，返回响应文本。"""
        last_error: Optional[Exception] = None
        for attempt in range(3):
            self._respect_delay()
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.get(self.api_base, params=params)
                    self._last_request_ts = time.time()
                    resp.raise_for_status()
                    return resp.text
            except Exception as exc:  # noqa: BLE001 - 统一重试
                last_error = exc
                backoff = 2**attempt
                logger.warning(
                    "arXiv request failed (attempt %d/3): %s; backoff %ds",
                    attempt + 1,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
        raise ArxivClientError(f"arXiv request failed after 3 retries: {last_error}")

    @staticmethod
    def parse_feed(feed_text: str) -> list[ArxivPaper]:
        """解析 Atom feed 文本为 ArxivPaper 列表。"""
        parsed = feedparser.parse(feed_text)
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            raise ArxivClientError(
                f"malformed arXiv feed: {getattr(parsed, 'bozo_exception', 'unknown')}"
            )
        return [normalize_entry(dict(e)) for e in parsed.entries]

    def search(self, query: str, max_results: Optional[int] = None) -> list[ArxivPaper]:
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results or self.max_results_per_profile,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        feed_text = self._raw_request(params)
        return self.parse_feed(feed_text)

    def fetch_window(
        self,
        categories: list[str],
        window_start: datetime,
        window_end: datetime,
        max_results: Optional[int] = None,
    ) -> list[ArxivPaper]:
        query = build_query(categories, window_start, window_end)
        logger.info("arXiv query: %s", query)
        return self.search(query, max_results=max_results)

    def fetch_by_ids(self, arxiv_ids: list[str]) -> list[ArxivPaper]:
        params = {"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)}
        feed_text = self._raw_request(params)
        return self.parse_feed(feed_text)
