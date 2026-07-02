"""数据访问层：对 SQLite 各表的结构化读写。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from src.models import ArxivPaper, DigestItem, PaperScore, PaperSummary


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, paper: ArxivPaper) -> int:
        """写入或更新论文，返回 paper_id。以 arxiv_id 唯一。"""
        cur = self.conn.execute(
            "SELECT id FROM papers WHERE arxiv_id = ?", (paper.arxiv_id,)
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])
        cur = self.conn.execute(
            """
            INSERT INTO papers (
                arxiv_id, arxiv_id_base, version, title, abstract,
                authors_json, categories_json, primary_category,
                published_at, updated_at, abs_url, pdf_url, raw_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                paper.arxiv_id,
                paper.arxiv_id_base,
                paper.version,
                paper.title,
                paper.abstract,
                json.dumps(paper.authors, ensure_ascii=False),
                json.dumps(paper.categories, ensure_ascii=False),
                paper.primary_category,
                paper.published_at,
                paper.updated_at,
                paper.abs_url,
                paper.pdf_url,
                json.dumps(paper.raw_json, ensure_ascii=False),
                _now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)


class ScoreRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, paper_id: int, score: PaperScore) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO paper_scores (
                paper_id, profile_id, keyword_score, semantic_score,
                llm_relevance_score, final_score, judge_label, judge_reason, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                paper_id,
                score.profile_id,
                score.keyword_score,
                score.semantic_score,
                score.llm_relevance_score,
                score.final_score,
                score.judge_label,
                score.judge_reason,
                _now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)


class SummaryRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(
        self,
        paper_id: int,
        summary: PaperSummary,
        summary_type: str,
        language: str,
        model_name: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO summaries (
                paper_id, profile_id, summary_type, language,
                summary_json, model_name, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                paper_id,
                summary.profile_id,
                summary_type,
                language,
                summary.model_dump_json(),
                model_name,
                _now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)


class DigestRunRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def start(self, run_id: str, window_start: str, window_end: str) -> None:
        self.conn.execute(
            """
            INSERT INTO digest_runs (run_id, started_at, status, window_start, window_end)
            VALUES (?,?,?,?,?)
            """,
            (run_id, _now_iso(), "running", window_start, window_end),
        )
        self.conn.commit()

    def finish(
        self,
        run_id: str,
        status: str,
        candidate_count: int = 0,
        selected_count: int = 0,
        delivered_chat_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE digest_runs
            SET finished_at = ?, status = ?, candidate_count = ?, selected_count = ?,
                delivered_chat_id = ?, error_message = ?
            WHERE run_id = ?
            """,
            (
                _now_iso(),
                status,
                candidate_count,
                selected_count,
                delivered_chat_id,
                error_message,
                run_id,
            ),
        )
        self.conn.commit()


class DeliveredItemRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def is_delivered(self, arxiv_id_base: str, profile_id: str, chat_id: str) -> bool:
        cur = self.conn.execute(
            """
            SELECT 1 FROM delivered_items
            WHERE arxiv_id_base = ? AND profile_id = ? AND chat_id = ?
            """,
            (arxiv_id_base, profile_id, chat_id),
        )
        return cur.fetchone() is not None

    def mark_delivered(
        self,
        arxiv_id_base: str,
        profile_id: str,
        chat_id: str,
        digest_run_id: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO delivered_items
                (arxiv_id_base, profile_id, chat_id, delivered_at, digest_run_id)
            VALUES (?,?,?,?,?)
            """,
            (arxiv_id_base, profile_id, chat_id, _now_iso(), digest_run_id),
        )
        self.conn.commit()


class FeedbackRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(
        self,
        arxiv_id_base: str,
        user_id: str,
        feedback_type: str,
        profile_id: Optional[str] = None,
        feedback_text: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO feedback
                (arxiv_id_base, profile_id, user_id, feedback_type, feedback_text, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (arxiv_id_base, profile_id, user_id, feedback_type, feedback_text, _now_iso()),
        )
        self.conn.commit()

    def types_for(self, arxiv_id_base: str, profile_id: Optional[str]) -> list[str]:
        cur = self.conn.execute(
            """
            SELECT feedback_type FROM feedback
            WHERE arxiv_id_base = ? AND (profile_id = ? OR profile_id IS NULL)
            """,
            (arxiv_id_base, profile_id),
        )
        return [r["feedback_type"] for r in cur.fetchall()]
