"""极简运行指标收集（用于 dry-run 打印与 run log）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DigestMetrics:
    candidate_count: int = 0
    dedup_dropped: int = 0
    keyword_rejected: int = 0
    selected_count: int = 0
    per_profile_selected: dict[str, int] = field(default_factory=dict)
    llm_completed: bool = True

    def as_dict(self) -> dict:
        return {
            "candidate_count": self.candidate_count,
            "dedup_dropped": self.dedup_dropped,
            "keyword_rejected": self.keyword_rejected,
            "selected_count": self.selected_count,
            "per_profile_selected": self.per_profile_selected,
            "llm_completed": self.llm_completed,
        }
