"""反馈调整。对应 schema 第 10.3 节 feedback_adjustment。

第一版只根据已记录的 feedback 计算分数调整，不训练模型。
"""

from __future__ import annotations

from typing import Optional

from src.storage.repositories import FeedbackRepository

FEEDBACK_ADJUSTMENT = {
    "must_read": 0.08,
    "relevant": 0.04,
    "irrelevant": -0.08,
    "skip": -0.12,
}


def adjustment_for(feedback_types: list[str]) -> float:
    """把该论文的所有 feedback 类型累加为一个调整值。"""
    return sum(FEEDBACK_ADJUSTMENT.get(ft, 0.0) for ft in feedback_types)


class FeedbackModel:
    def __init__(self, repo: Optional[FeedbackRepository] = None):
        self.repo = repo

    def adjustment(self, arxiv_id_base: str, profile_id: Optional[str]) -> float:
        if self.repo is None:
            return 0.0
        types = self.repo.types_for(arxiv_id_base, profile_id)
        return adjustment_for(types)
