"""把 arXiv Atom feed entry 归一化为 ArxivPaper。对应 schema 第 9.2 节去重规则。"""

from __future__ import annotations

import re

from src.models import ArxivPaper

_ID_VERSION_RE = re.compile(r"v(\d+)$")


def split_arxiv_id(arxiv_id: str) -> tuple[str, int | None]:
    """拆分带版本的 arxiv_id。

    例如 '2607.01234v2' -> ('2607.01234', 2)
         '2607.01234'   -> ('2607.01234', None)
         'cs/0501001v1' -> ('cs/0501001', 1)
    """
    m = _ID_VERSION_RE.search(arxiv_id)
    if m:
        base = arxiv_id[: m.start()]
        return base, int(m.group(1))
    return arxiv_id, None


def extract_arxiv_id_from_url(url_or_id: str) -> str | None:
    """从 arXiv URL 或裸 ID 提取 arxiv_id（可能带版本）。"""
    text = url_or_id.strip()
    # https://arxiv.org/abs/2607.01234v1 或 /pdf/2607.01234
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).replace(".pdf", "")
    # 裸 ID：2607.01234 或 2607.01234v2 或旧式 cs/0501001
    m = re.match(r"^([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?$", text, re.IGNORECASE)
    if m:
        return text
    return None


def normalize_entry(entry: dict) -> ArxivPaper:
    """把 feedparser 解析出的 entry(dict) 归一化为 ArxivPaper。"""
    raw_id = entry.get("id", "")
    # entry.id 形如 http://arxiv.org/abs/2607.01234v1
    short = extract_arxiv_id_from_url(raw_id) or raw_id.rsplit("/", 1)[-1]
    base, version = split_arxiv_id(short)

    authors = [a.get("name", "") for a in entry.get("authors", []) if a.get("name")]

    tags = entry.get("tags", []) or []
    categories = [t.get("term") for t in tags if t.get("term")]
    primary = None
    if entry.get("arxiv_primary_category"):
        primary = entry["arxiv_primary_category"].get("term")
    elif categories:
        primary = categories[0]

    abs_url = ""
    pdf_url = None
    for link in entry.get("links", []) or []:
        if link.get("type") == "application/pdf" or link.get("title") == "pdf":
            pdf_url = link.get("href")
        elif link.get("rel") == "alternate":
            abs_url = link.get("href", "")
    if not abs_url:
        abs_url = f"https://arxiv.org/abs/{short}"
    if not pdf_url:
        pdf_url = f"https://arxiv.org/pdf/{base}"

    title = " ".join((entry.get("title") or "").split())
    abstract = " ".join((entry.get("summary") or "").split())

    return ArxivPaper(
        arxiv_id=short,
        arxiv_id_base=base,
        version=version,
        title=title,
        abstract=abstract,
        authors=authors,
        categories=categories,
        primary_category=primary,
        published_at=entry.get("published"),
        updated_at=entry.get("updated"),
        abs_url=abs_url,
        pdf_url=pdf_url,
        raw_json={
            "id": raw_id,
            "title": title,
            "summary": abstract,
            "published": entry.get("published"),
            "updated": entry.get("updated"),
            "authors": authors,
            "categories": categories,
        },
    )
