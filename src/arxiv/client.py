"""arXiv API 客户端。对应 schema 第 9 节。

- 使用 https://export.arxiv.org/api/query
- sortBy=submittedDate, sortOrder=descending
- 连续请求间隔 ARXIV_REQUEST_DELAY_SECONDS
- 失败最多重试 3 次，指数退避
- 返回归一化的 ArxivPaper 列表
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import feedparser
import httpx

from src.arxiv.normalizer import normalize_entry
from src.arxiv.query_builder import build_query
from src.config import REPO_ROOT, get_config
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
        self.cache_ttl = cfg.arxiv_cache_ttl_seconds
        cache_dir = Path(cfg.cache_dir)
        if not cache_dir.is_absolute():
            cache_dir = REPO_ROOT / cache_dir
        self._cache_dir = cache_dir / "arxiv"

    # ---- 抓取缓存（减少 arXiv 请求 / 防限流封禁） ----
    def _cache_path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / f"{h}.json"

    def _cache_get(self, key: str) -> Optional[str]:
        if self.cache_ttl <= 0:
            return None
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(data.get("ts", 0)) <= self.cache_ttl:
                logger.info("arXiv 命中缓存：%s", key)
                return data.get("text")
        except Exception:  # noqa: BLE001
            return None
        return None

    def _cache_put(self, key: str, text: str) -> None:
        if self.cache_ttl <= 0:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(key).write_text(
                json.dumps({"ts": time.time(), "key": key, "text": text}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("写 arXiv 缓存失败：%s", exc)

    def _respect_delay(self) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < self.request_delay_seconds:
            time.sleep(self.request_delay_seconds - elapsed)

    def _raw_request(self, params: dict) -> str:
        """执行带重试的 HTTP GET，返回响应文本。"""
        last_error: Optional[Exception] = None
        max_attempts = 5
        for attempt in range(max_attempts):
            self._respect_delay()
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.get(self.api_base, params=params)
                    self._last_request_ts = time.time()
                    resp.raise_for_status()
                    return resp.text
            except Exception as exc:  # noqa: BLE001 - 统一重试
                last_error = exc
                # 429（限流）需更有耐心：尊重 Retry-After，否则较长线性退避
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 429:
                    retry_after = getattr(exc, "response", None).headers.get("Retry-After") if getattr(exc, "response", None) else None
                    try:
                        backoff = int(retry_after) if retry_after else 20 * (attempt + 1)
                    except (TypeError, ValueError):
                        backoff = 20 * (attempt + 1)
                else:
                    backoff = 2**attempt
                logger.warning(
                    "arXiv request failed (attempt %d/%d, status=%s): %s; backoff %ds",
                    attempt + 1,
                    max_attempts,
                    status,
                    exc,
                    backoff,
                )
                self._last_request_ts = time.time()  # 退避也算入限速基准
                time.sleep(backoff)
        raise ArxivClientError(
            f"arXiv request failed after {max_attempts} retries: {last_error}"
        )

    @staticmethod
    def parse_feed(feed_text: str) -> list[ArxivPaper]:
        """解析 Atom feed 文本为 ArxivPaper 列表。"""
        parsed = feedparser.parse(feed_text)
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            raise ArxivClientError(
                f"malformed arXiv feed: {getattr(parsed, 'bozo_exception', 'unknown')}"
            )
        return [normalize_entry(dict(e)) for e in parsed.entries]

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        cache_key: Optional[str] = None,
    ) -> list[ArxivPaper]:
        n = max_results or self.max_results_per_profile
        key = cache_key or f"q:{query}:{n}"
        cached = self._cache_get(key)
        if cached is not None:
            return self.parse_feed(cached)
        params = {
            "search_query": query,
            "start": 0,
            "max_results": n,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        feed_text = self._raw_request(params)
        self._cache_put(key, feed_text)
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
        n = max_results or self.max_results_per_profile
        # 缓存键按分类粗粒度（忽略精确到分钟的窗口），使短期内重复运行复用、每天则取新
        cache_key = f"win:{'|'.join(sorted(categories))}:{n}"
        return self.search(query, max_results=max_results, cache_key=cache_key)

    def fetch_by_ids(self, arxiv_ids: list[str]) -> list[ArxivPaper]:
        key = f"ids:{','.join(sorted(arxiv_ids))}"
        cached = self._cache_get(key)
        if cached is not None:
            return self.parse_feed(cached)
        params = {"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)}
        feed_text = self._raw_request(params)
        self._cache_put(key, feed_text)
        return self.parse_feed(feed_text)
