"""相关性排序器：融合关键词层、语义层、LLM judge 层与反馈调整。

对应 schema 第 10 节。最终分数：
    final_score = keyword_weight * normalized_keyword_score
                + semantic_weight * semantic_score
                + llm_judge_weight * llm_relevance_score
                + feedback_adjustment
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config import RankingSettings, get_config
from src.models import ArxivPaper, PaperScore, ResearchProfile
from src.relevance.embeddings import EmbeddingModel
from src.relevance.feedback_model import FeedbackModel
from src.relevance.keyword_filter import score_keywords
from src.relevance.llm_judge import LlmJudge
from src.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RankedPaper:
    paper: ArxivPaper
    score: PaperScore


def _paper_doc(paper: ArxivPaper) -> str:
    return "\n".join([paper.title, paper.abstract, " ".join(paper.categories)])


def _normalize_keyword(keyword_score: float) -> float:
    # keyword_score ∈ [-1, 1] -> [0, 1]
    return (keyword_score + 1.0) / 2.0


class RelevanceRanker:
    def __init__(
        self,
        ranking: Optional[RankingSettings] = None,
        embedding_model: Optional[EmbeddingModel] = None,
        judge: Optional[LlmJudge] = None,
        feedback_model: Optional[FeedbackModel] = None,
    ):
        self.ranking = ranking or get_config().ranking
        self.embedding_model = embedding_model or EmbeddingModel()
        self.judge = judge or LlmJudge()
        self.feedback_model = feedback_model or FeedbackModel()
        # 有真实 embedding 时走语义层；否则走 LLM-judge 主导模式。
        self.semantic_available = not self.embedding_model.uses_fallback

    def _semantic_scores(
        self, profile: ResearchProfile, papers: list[ArxivPaper]
    ) -> list[float]:
        if not papers:
            return []
        texts = [profile.semantic_query] + [_paper_doc(p) for p in papers]
        vectors = self.embedding_model.embed_texts(texts)
        from src.relevance.embeddings import cosine

        profile_vec = vectors[0]
        return [cosine(profile_vec, v) for v in vectors[1:]]

    def rank(
        self, profile: ResearchProfile, papers: list[ArxivPaper]
    ) -> list[RankedPaper]:
        if not papers:
            return []
        if self.semantic_available:
            return self._rank_semantic(profile, papers)
        return self._rank_judge_primary(profile, papers)

    def _rank_semantic(
        self, profile: ResearchProfile, papers: list[ArxivPaper]
    ) -> list[RankedPaper]:
        r = self.ranking
        semantic_scores = self._semantic_scores(profile, papers)

        # 阶段一：关键词 + 语义，决定是否进入 LLM judge。
        stage_one: list[tuple[ArxivPaper, float, float]] = []
        for paper, sem in zip(papers, semantic_scores):
            kw = score_keywords(paper, profile)
            # 负关键词命中 >=2 且语义分未达 medium：直接淘汰，不调用 LLM。
            if kw.reject_candidate and sem < r.semantic_medium_below:
                continue
            stage_one.append((paper, kw.keyword_score, sem))

        # 送 judge 的预算按 keyword+semantic 组合分排序，确保关键词强相关论文一定被判到，
        # 弥补 embedding 区分度不足（如 bge 相似度普遍偏高、拉不开差距）。
        stage_one.sort(
            key=lambda x: 0.5 * _normalize_keyword(x[1]) + 0.5 * x[2], reverse=True
        )
        judge_budget = min(len(stage_one), r.judge_budget)

        ranked: list[RankedPaper] = []
        for idx, (paper, kw_score, sem) in enumerate(stage_one):
            use_llm = idx < judge_budget and sem >= r.semantic_reject_below
            if use_llm:
                judged = self.judge.judge(paper, profile, sem)
            else:
                judged = self.judge._fallback(sem, reason="未进入 LLM judge 预算，使用语义分")

            final = (
                r.keyword_weight * _normalize_keyword(kw_score)
                + r.semantic_weight * sem
                + r.llm_judge_weight * judged.relevance_score
                + self.feedback_model.adjustment(paper.arxiv_id_base, profile.id)
            )
            ranked.append(
                self._make_ranked(profile, paper, kw_score, sem, judged, final)
            )

        # 真实 LLM judge 过的论文优先，避免未判定的 fallback 论文虚高污染排序。
        ranked.sort(key=lambda rp: (rp.score.llm_judged, rp.score.final_score), reverse=True)
        return ranked

    def _rank_judge_primary(
        self, profile: ResearchProfile, papers: list[ArxivPaper]
    ) -> list[RankedPaper]:
        """无 embedding 时：keyword 预筛 + LLM judge 主导打分。"""
        r = self.ranking

        stage_one: list[tuple[ArxivPaper, float]] = []
        for paper in papers:
            kw = score_keywords(paper, profile)
            if kw.reject_candidate:  # 负关键词命中 >=2，无语义可救，直接淘汰
                continue
            stage_one.append((paper, kw.keyword_score))

        # 关键词分高者优先进入 LLM judge 预算；稳定排序保留 arXiv 的时间倒序。
        stage_one.sort(key=lambda x: x[1], reverse=True)
        judge_budget = min(len(stage_one), r.judge_budget)

        ranked: list[RankedPaper] = []
        for idx, (paper, kw_score) in enumerate(stage_one):
            if idx < judge_budget:
                judged = self.judge.judge(paper, profile, semantic_score=0.0)
            else:
                # 超预算：不判为可选（label=reject），避免遗漏但控制成本。
                judged = self.judge._fallback(0.0, reason="超出无-embedding judge 预算")

            final = (
                r.judge_primary_keyword_weight * _normalize_keyword(kw_score)
                + r.judge_primary_judge_weight * judged.relevance_score
                + self.feedback_model.adjustment(paper.arxiv_id_base, profile.id)
            )
            ranked.append(
                self._make_ranked(profile, paper, kw_score, 0.0, judged, final)
            )

        ranked.sort(key=lambda rp: (rp.score.llm_judged, rp.score.final_score), reverse=True)
        return ranked

    @staticmethod
    def _make_ranked(profile, paper, kw_score, sem, judged, final) -> RankedPaper:
        final = round(max(0.0, min(1.0, final)), 4)
        score = PaperScore(
            profile_id=profile.id,
            keyword_score=round(kw_score, 4),
            semantic_score=round(sem, 4),
            llm_relevance_score=round(judged.relevance_score, 4),
            final_score=final,
            judge_label=judged.label,
            judge_reason=judged.reason,
            matched_aspects=judged.matched_aspects,
            mismatch_aspects=judged.mismatch_aspects,
            llm_judged=judged.llm_completed,
        )
        return RankedPaper(paper=paper, score=score)

    def select(
        self, profile: ResearchProfile, ranked: list[RankedPaper]
    ) -> list[RankedPaper]:
        """按 profile 阈值与 top_k 过滤入选论文。"""
        r = self.ranking
        min_final = profile.min_final_score or r.min_final_score
        selected = []
        for rp in ranked:
            # 只允许真实 LLM judge 过的论文入选，避免 fallback 误判。
            if not rp.score.llm_judged:
                continue
            if rp.score.final_score < min_final or rp.score.judge_label == "reject":
                continue
            # 语义模式下额外要求语义分达标；judge-primary 模式跳过该门槛。
            if self.semantic_available and rp.score.semantic_score < r.min_semantic_score:
                continue
            selected.append(rp)
        return selected[: profile.top_k]
