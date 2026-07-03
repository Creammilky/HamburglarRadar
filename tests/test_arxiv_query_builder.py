from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.arxiv.normalizer import (
    extract_arxiv_id_from_url,
    normalize_entry,
    split_arxiv_id,
)
from src.arxiv.client import ArxivClient
from src.arxiv.query_builder import build_query, format_arxiv_time


def test_format_arxiv_time_converts_to_utc():
    sgt = ZoneInfo("Asia/Singapore")
    # 新加坡 08:30 == 00:30 UTC
    dt = datetime(2026, 7, 2, 8, 30, tzinfo=sgt)
    assert format_arxiv_time(dt) == "202607020030"


def test_build_query_time_range_and_categories():
    start = datetime(2026, 7, 1, 0, 30, tzinfo=timezone.utc)
    end = datetime(2026, 7, 2, 0, 30, tzinfo=timezone.utc)
    query = build_query(["cs.AI", "cs.CR"], start, end)
    assert "cat:cs.AI OR cat:cs.CR" in query
    assert "submittedDate:[202607010030 TO 202607020030]" in query


def test_split_arxiv_id_base_and_version():
    assert split_arxiv_id("2607.01234v1") == ("2607.01234", 1)
    assert split_arxiv_id("2607.01234v12") == ("2607.01234", 12)
    assert split_arxiv_id("2607.01234") == ("2607.01234", None)
    assert split_arxiv_id("cs/0501001v2") == ("cs/0501001", 2)


def test_extract_arxiv_id_from_url():
    assert extract_arxiv_id_from_url("https://arxiv.org/abs/2607.01234v1") == "2607.01234v1"
    assert extract_arxiv_id_from_url("https://arxiv.org/pdf/2607.01234") == "2607.01234"
    assert extract_arxiv_id_from_url("2607.01234") == "2607.01234"
    assert extract_arxiv_id_from_url("not a paper") is None


def test_fetch_cache_avoids_second_http(tmp_path, monkeypatch):
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "arxiv_feed_sample.xml"
    feed = fixture.read_text(encoding="utf-8")

    client = ArxivClient()
    client._cache_dir = tmp_path / "arxiv"  # 隔离缓存目录
    client.cache_ttl = 3600

    calls = {"n": 0}

    def fake_raw(params):
        calls["n"] += 1
        return feed

    monkeypatch.setattr(client, "_raw_request", fake_raw)

    r1 = client.search("all:test", max_results=5)
    r2 = client.search("all:test", max_results=5)  # 应命中缓存
    assert len(r1) == 2 and len(r2) == 2
    assert calls["n"] == 1  # 只请求了一次，第二次走缓存


def test_cache_disabled_when_ttl_zero(tmp_path, monkeypatch):
    from pathlib import Path

    feed = (Path(__file__).parent / "fixtures" / "arxiv_feed_sample.xml").read_text(encoding="utf-8")
    client = ArxivClient()
    client._cache_dir = tmp_path / "arxiv"
    client.cache_ttl = 0
    calls = {"n": 0}
    monkeypatch.setattr(client, "_raw_request", lambda p: (calls.__setitem__("n", calls["n"] + 1) or feed))
    client.search("all:x", max_results=5)
    client.search("all:x", max_results=5)
    assert calls["n"] == 2  # 关闭缓存 → 每次都请求


def test_parse_feed_fixture(tmp_path):
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "arxiv_feed_sample.xml"
    papers = ArxivClient.parse_feed(fixture.read_text(encoding="utf-8"))
    assert len(papers) == 2
    first = papers[0]
    assert first.arxiv_id == "2607.01234v1"
    assert first.arxiv_id_base == "2607.01234"
    assert first.version == 1
    assert first.primary_category == "cs.CR"
    assert "cs.CR" in first.categories
    assert first.abs_url.endswith("2607.01234v1")
