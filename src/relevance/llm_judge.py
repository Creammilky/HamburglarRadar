"""LLM Judge 层。对应 schema 第 10.3 与 17.1 节。

只对初筛后的 top 论文调用，减少成本。
LLM 输出严格 JSON；解析失败时 fallback 到基于语义分的启发式判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config import load_prompt, render_prompt
from src.llm import LlmClient, LlmError
from src.models import ArxivPaper, ResearchProfile
from src.observability.logger import get_logger

logger = get_logger(__name__)

_VALID_LABELS = {"high", "medium", "low", "reject"}
_VALID_COLLISION = {"high", "medium", "low", "unknown"}


@dataclass
class JudgeResult:
    label: str
    relevance_score: float
    reason: str
    matched_aspects: list[str]
    mismatch_aspects: list[str]
    collision_risk: str
    llm_completed: bool


class LlmJudge:
    def __init__(self, client: Optional[LlmClient] = None):
        self.client = client or LlmClient()
        self._prompt = load_prompt("relevance_judge")

    def judge(
        self,
        paper: ArxivPaper,
        profile: ResearchProfile,
        semantic_score: float,
    ) -> JudgeResult:
        if not self.client.chat_enabled:
            return self._fallback(semantic_score, reason="未配置 LLM，使用语义分回退判断")

        user = render_prompt(
            self._prompt,
            profile_name=profile.name,
            semantic_query=profile.semantic_query,
            title=paper.title,
            abstract=paper.abstract,
            categories=", ".join(paper.categories),
        )
        try:
            data = self.client.chat_json(system="你是严谨的论文筛选助手。", user=user)
        except (LlmError, Exception) as exc:  # noqa: BLE001
            logger.warning("LLM judge failed, fallback: %s", exc)
            return self._fallback(semantic_score, reason="LLM judge 失败，使用语义分回退")

        return self._parse(data, semantic_score)

    @staticmethod
    def _parse(data: dict, semantic_score: float) -> JudgeResult:
        label = str(data.get("label", "")).lower()
        if label not in _VALID_LABELS:
            label = "low"
        try:
            score = float(data.get("relevance_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))
        collision = str(data.get("collision_risk", "unknown")).lower()
        if collision not in _VALID_COLLISION:
            collision = "unknown"
        return JudgeResult(
            label=label,
            relevance_score=score,
            reason=str(data.get("reason", "")),
            matched_aspects=list(data.get("matched_aspects", []) or []),
            mismatch_aspects=list(data.get("mismatch_aspects", []) or []),
            collision_risk=collision,
            llm_completed=True,
        )

    @staticmethod
    def _fallback(semantic_score: float, reason: str) -> JudgeResult:
        # 基于语义分映射 label 与 relevance_score
        if semantic_score >= 0.48:
            label = "high"
        elif semantic_score >= 0.35:
            label = "medium"
        elif semantic_score >= 0.25:
            label = "low"
        else:
            label = "reject"
        return JudgeResult(
            label=label,
            relevance_score=round(max(0.0, min(1.0, semantic_score)), 4),
            reason=reason,
            matched_aspects=[],
            mismatch_aspects=[],
            collision_risk="unknown",
            llm_completed=False,
        )
