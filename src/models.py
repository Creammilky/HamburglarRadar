"""领域数据模型（Pydantic）。对应 schema 第 8 节与相关章节。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ResearchProfile(BaseModel):
    id: str
    name: str
    enabled: bool = True
    arxiv_categories: list[str]
    semantic_query: str
    positive_keywords: list[str] = []
    negative_keywords: list[str] = []
    min_final_score: float = 0.62
    top_k: int = 8


class ArxivPaper(BaseModel):
    arxiv_id: str
    arxiv_id_base: str
    version: Optional[int] = None
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    primary_category: Optional[str] = None
    published_at: Optional[str] = None
    updated_at: Optional[str] = None
    abs_url: str
    pdf_url: Optional[str] = None
    raw_json: dict = Field(default_factory=dict)


class PaperScore(BaseModel):
    profile_id: str
    keyword_score: float
    semantic_score: float
    llm_relevance_score: Optional[float] = None
    final_score: float
    judge_label: Literal["high", "medium", "low", "reject"]
    judge_reason: str = ""
    matched_aspects: list[str] = []
    mismatch_aspects: list[str] = []
    # 是否经过真实 LLM judge（仅 LLM 判定过的论文才允许入选，保证精度）
    llm_judged: bool = True


class PaperSummary(BaseModel):
    arxiv_id: str
    profile_id: Optional[str] = None
    title_zh: str = ""
    one_sentence: str
    problem: str
    method: str
    main_findings: str
    why_relevant: str
    limitations: str
    collision_risk: Literal["high", "medium", "low", "unknown"] = "unknown"
    recommended_action: Literal["must_read", "skim", "archive", "skip"] = "archive"
    llm_completed: bool = True


class DigestItem(BaseModel):
    paper: ArxivPaper
    score: PaperScore
    summary: PaperSummary


class DigestReport(BaseModel):
    run_id: str
    window_start: str
    window_end: str
    profile_name: str
    profile_id: str
    items: list[DigestItem]


class CommandIntent(BaseModel):
    intent: Literal[
        "daily_digest_now",
        "search_topic",
        "summarize_paper",
        "collision_check",
        "save_paper",
        "feedback",
        "update_profile",
        "help",
        "unknown",
    ]
    arxiv_ids: list[str] = []
    urls: list[str] = []
    topic: Optional[str] = None
    feedback_type: Optional[str] = None
    raw_text: str = ""
