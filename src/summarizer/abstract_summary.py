"""基于 arXiv title + abstract 的中文结构化摘要。对应 schema 第 11 与 17.2 节。

摘要原则：
- 默认只基于 title + abstract，不下载 PDF。
- 不编造摘要之外的信息；未说明处写“摘要未说明”。
- 未配置/失败时使用文本 fallback，并标记 llm_completed=False。
"""

from __future__ import annotations

from typing import Optional

from src.config import load_prompt, render_prompt
from src.llm import LlmClient, LlmError
from src.models import ArxivPaper, PaperScore, PaperSummary, ResearchProfile
from src.observability.logger import get_logger

logger = get_logger(__name__)

_VALID_ACTION = {"must_read", "skim", "archive", "skip"}
_VALID_COLLISION = {"high", "medium", "low", "unknown"}

_LABEL_TO_ACTION = {
    "high": "must_read",
    "medium": "skim",
    "low": "archive",
    "reject": "skip",
}


class AbstractSummarizer:
    def __init__(self, client: Optional[LlmClient] = None):
        self.client = client or LlmClient()
        self._prompt = load_prompt("paper_summary")

    @property
    def model_name(self) -> str:
        return self.client.env.llm_chat_model if self.client.chat_enabled else "fallback"

    def summarize(
        self,
        paper: ArxivPaper,
        profile: ResearchProfile,
        score: PaperScore,
    ) -> PaperSummary:
        if self.client.chat_enabled:
            summary = self._summarize_llm(paper, profile, score)
            if summary is not None:
                return summary
        return self._fallback(paper, profile, score)

    def _summarize_llm(
        self, paper: ArxivPaper, profile: ResearchProfile, score: PaperScore
    ) -> Optional[PaperSummary]:
        user = render_prompt(
            self._prompt,
            title=paper.title,
            abstract=paper.abstract,
            profile_name=profile.name,
            semantic_query=profile.semantic_query,
        )
        try:
            data = self.client.chat_json(system="你是严谨的研究助理。", user=user)
        except (LlmError, Exception) as exc:  # noqa: BLE001
            logger.warning("LLM summary failed, fallback: %s", exc)
            return None

        action = str(data.get("recommended_action", "")).lower()
        if action not in _VALID_ACTION:
            action = _LABEL_TO_ACTION.get(score.judge_label, "archive")
        collision = str(data.get("collision_risk", "unknown")).lower()
        if collision not in _VALID_COLLISION:
            collision = "unknown"

        return PaperSummary(
            arxiv_id=paper.arxiv_id,
            profile_id=profile.id,
            title_zh=str(data.get("title_zh", "")).strip(),
            one_sentence=str(data.get("one_sentence", "")).strip(),
            problem=str(data.get("problem", "")).strip(),
            method=str(data.get("method", "")).strip(),
            main_findings=str(data.get("main_findings", "")).strip(),
            why_relevant=str(data.get("why_relevant", "")).strip(),
            limitations=str(data.get("limitations", "")).strip(),
            collision_risk=collision,
            recommended_action=action,
            llm_completed=True,
        )

    def _fallback(
        self, paper: ArxivPaper, profile: ResearchProfile, score: PaperScore
    ) -> PaperSummary:
        """无 LLM 时的文本 fallback，不编造内容。"""
        first_sentence = paper.abstract.split(". ")[0].strip()
        if len(first_sentence) > 200:
            first_sentence = first_sentence[:200] + "…"
        note = "（未完成 LLM 总结）"
        matched = ", ".join(
            kw for kw in profile.positive_keywords if kw.lower() in paper.abstract.lower()
        )
        why = f"命中方向关键词：{matched}。" if matched else "语义与该方向相关。"
        return PaperSummary(
            arxiv_id=paper.arxiv_id,
            profile_id=profile.id,
            title_zh="",
            one_sentence=(first_sentence or "摘要未说明") + note,
            problem="摘要未说明" + note,
            method="摘要未说明" + note,
            main_findings="摘要未说明" + note,
            why_relevant=why,
            limitations="摘要未说明" + note,
            collision_risk="unknown",
            recommended_action=_LABEL_TO_ACTION.get(score.judge_label, "archive"),
            llm_completed=False,
        )
