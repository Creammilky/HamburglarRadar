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
