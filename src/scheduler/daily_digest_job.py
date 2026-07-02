"""每日 08:30 晨报作业。对应 schema 第 3.1、16、19、21 节。

用法：
    python -m src.scheduler.daily_digest_job --dry-run
    python -m src.scheduler.daily_digest_job --send
    python -m src.scheduler.daily_digest_job --send --profile llm_security_range
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from src.arxiv.client import ArxivClient, ArxivClientError
import json

from src.config import REPO_ROOT, AppConfig, get_config
from src.feishu import card_renderer
from src.feishu.message_sender import MessageSender
from src.models import ArxivPaper, DigestItem, DigestReport, ResearchProfile
from src.observability.logger import get_logger
from src.observability.metrics import DigestMetrics
from src.relevance.feedback_model import FeedbackModel
from src.relevance.ranker import RankedPaper, RelevanceRanker
from src.storage.db import get_connection, init_db
from src.storage.repositories import (
    DeliveredItemRepository,
    DigestRunRepository,
    FeedbackRepository,
    PaperRepository,
    ScoreRepository,
    SummaryRepository,
)
from src.summarizer.abstract_summary import AbstractSummarizer

logger = get_logger(__name__)


_DIGEST_CACHE = REPO_ROOT / "data" / "last_digest.json"


def _write_digest_cache(date_str: str, text: str, card: dict) -> None:
    try:
        _DIGEST_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _DIGEST_CACHE.write_text(
            json.dumps({"date": date_str, "text": text, "card": card}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("写晨报缓存失败：%s", exc)


def load_cached_digest() -> Optional[dict]:
    """返回最近一次构建的晨报缓存 {date, text, card}，无则 None。"""
    if not _DIGEST_CACHE.exists():
        return None
    try:
        return json.loads(_DIGEST_CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


@dataclass
class BuildResult:
    run_id: str
    reports: list[DigestReport]
    metrics: DigestMetrics
    text: str
    card: dict
    window_start: str
    window_end: str


def _parse_paper_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _dedup_by_base(papers: list[ArxivPaper]) -> list[ArxivPaper]:
    """按 arxiv_id_base 去重，保留最高版本（None 视为最高，因指定版本抓取才带版本）。"""
    best: dict[str, ArxivPaper] = {}
    for p in papers:
        cur = best.get(p.arxiv_id_base)
        if cur is None:
            best[p.arxiv_id_base] = p
            continue
        if (p.version or 0) > (cur.version or 0):
            best[p.arxiv_id_base] = p
    return list(best.values())


class DailyDigest:
    def __init__(self, config: Optional[AppConfig] = None, dry_run: bool = True):
        self.config = config or get_config()
        self.dry_run = dry_run
        self.tz = ZoneInfo(self.config.app.timezone)
        self.client = ArxivClient()
        self.summarizer = AbstractSummarizer()
        # dry-run 不接数据库；send 模式接入 feedback 用于分数调整。
        self._conn = None
        feedback_model = FeedbackModel()
        if not dry_run:
            init_db()
            self._conn = get_connection()
            feedback_model = FeedbackModel(FeedbackRepository(self._conn))
        self.ranker = RelevanceRanker(feedback_model=feedback_model)
        # dry-run 调试用：记录每个 profile 的 top 候选（含分数），便于展示评分。
        self.debug_ranked: dict[str, list[RankedPaper]] = {}

    def _windows(self) -> tuple[datetime, datetime, datetime]:
        from src.timesource import now_local

        now = now_local(self.tz)
        fetch_start = now - timedelta(hours=self.config.app.fetch_lookback_hours)
        display_start = now - timedelta(hours=self.config.app.digest_lookback_hours)
        return now, fetch_start, display_start

    def _build_profile_report(
        self,
        profile: ResearchProfile,
        run_id: str,
        now: datetime,
        fetch_start: datetime,
        display_start_utc: datetime,
        now_utc: datetime,
        chat_id: str,
        metrics: DigestMetrics,
    ) -> DigestReport:
        papers = self.client.fetch_window(
            profile.arxiv_categories, fetch_start, now
        )
        papers = _dedup_by_base(papers)

        # 展示窗口：只保留过去 24h 内发布/更新的论文。
        in_window: list[ArxivPaper] = []
        for p in papers:
            pub = _parse_paper_time(p.published_at) or _parse_paper_time(p.updated_at)
            if pub is None or display_start_utc <= pub <= now_utc:
                in_window.append(p)
        metrics.candidate_count += len(in_window)

        ranked = self.ranker.rank(profile, in_window)
        self.debug_ranked[profile.id] = ranked[:10]
        selected = self.ranker.select(profile, ranked)

        # send 模式：去掉已投递过的论文，避免重复推送。
        if not self.dry_run and self._conn is not None and chat_id:
            delivered_repo = DeliveredItemRepository(self._conn)
            selected = [
                rp
                for rp in selected
                if not delivered_repo.is_delivered(
                    rp.paper.arxiv_id_base, profile.id, chat_id
                )
            ]

        items: list[DigestItem] = []
        for rp in selected:
            summary = self.summarizer.summarize(rp.paper, profile, rp.score)
            if not summary.llm_completed:
                metrics.llm_completed = False
            items.append(DigestItem(paper=rp.paper, score=rp.score, summary=summary))
            if not self.dry_run:
                self._persist_item(rp, summary, run_id, profile.id, chat_id)

        metrics.selected_count += len(items)
        metrics.per_profile_selected[profile.id] = len(items)

        return DigestReport(
            run_id=run_id,
            window_start=display_start_utc.isoformat(),
            window_end=now_utc.isoformat(),
            profile_name=profile.name,
            profile_id=profile.id,
            items=items,
        )

    def _persist_item(
        self,
        rp: RankedPaper,
        summary,
        run_id: str,
        profile_id: str,
        chat_id: str,
    ) -> None:
        assert self._conn is not None
        paper_repo = PaperRepository(self._conn)
        score_repo = ScoreRepository(self._conn)
        summary_repo = SummaryRepository(self._conn)
        paper_id = paper_repo.upsert(rp.paper)
        score_repo.insert(paper_id, rp.score)
        summary_repo.insert(
            paper_id,
            summary,
            summary_type="abstract",
            language=self.config.app.language,
            model_name=self.summarizer.model_name,
        )

    def build(
        self,
        profile_id: Optional[str] = None,
        delivery_id: str = "",
        run_id: Optional[str] = None,
    ) -> BuildResult:
        """构建晨报（抓取→筛选→摘要→渲染），不发送。可能抛出 ArxivClientError。"""
        run_id = run_id or str(uuid.uuid4())
        now, fetch_start, display_start = self._windows()
        now_utc = now.astimezone(timezone.utc)
        display_start_utc = display_start.astimezone(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        metrics = DigestMetrics()

        profiles = [p for p in self.config.profiles if p.enabled]
        if profile_id:
            profiles = [p for p in profiles if p.id == profile_id]

        reports: list[DigestReport] = []
        for profile in profiles:
            reports.append(
                self._build_profile_report(
                    profile,
                    run_id,
                    now,
                    fetch_start,
                    display_start_utc,
                    now_utc,
                    delivery_id,
                    metrics,
                )
            )

        text = card_renderer.render_text(reports, date_str)
        card = card_renderer.render_card(reports, date_str)
        _write_digest_cache(date_str, text, card)
        return BuildResult(
            run_id=run_id,
            reports=reports,
            metrics=metrics,
            text=text,
            card=card,
            window_start=display_start_utc.isoformat(),
            window_end=now_utc.isoformat(),
        )

    def run(self, profile_id: Optional[str] = None) -> int:
        run_id = str(uuid.uuid4())
        # send_chat_id 用于实际发送（Hermes 需要）；delivery_id 用于去重记录。
        send_chat_id = self.config.env.feishu_home_chat_id
        delivery_id = send_chat_id or (
            "feishu_webhook" if self.config.env.feishu_webhook_url else ""
        )

        enabled = [p for p in self.config.profiles if p.enabled]
        if profile_id:
            enabled = [p for p in enabled if p.id == profile_id]
        if not enabled:
            logger.error("没有匹配的启用 profile：%s", profile_id or "(all)")
            return 2

        run_repo = None
        if not self.dry_run and self._conn is not None:
            run_repo = DigestRunRepository(self._conn)

        try:
            br = self.build(profile_id, delivery_id, run_id)
        except ArxivClientError as exc:
            logger.error("arXiv 抓取失败：%s", exc)
            if run_repo is not None:
                run_repo.start(run_id, "", "")
                run_repo.finish(run_id, status="failed", error_message=str(exc))
            self._notify_fetch_failure(send_chat_id, str(exc))
            return 3

        if run_repo is not None:
            run_repo.start(run_id, br.window_start, br.window_end)

        if self.dry_run:
            self._print_dry_run(br.reports, br.metrics, br.text)
            return 0

        return self._deliver(
            run_id, run_repo, send_chat_id, delivery_id, br.card, br.text,
            br.metrics, br.reports,
        )

    def _deliver(
        self, run_id, run_repo, send_chat_id, delivery_id, card, text, metrics, reports
    ) -> int:
        sender = MessageSender(self.config, dry_run=False)
        result = sender.send_digest(send_chat_id, card, text)

        status = "success" if result.ok else "delivery_failed"
        if run_repo is not None:
            run_repo.finish(
                run_id,
                status=status,
                candidate_count=metrics.candidate_count,
                selected_count=metrics.selected_count,
                delivered_chat_id=delivery_id or None,
                error_message=None if result.ok else result.detail,
            )
        if result.ok and self._conn is not None and delivery_id:
            delivered_repo = DeliveredItemRepository(self._conn)
            for report in reports:
                for item in report.items:
                    delivered_repo.mark_delivered(
                        item.paper.arxiv_id_base, report.profile_id, delivery_id, run_id
                    )

        logger.info(
            "晨报发送：channel=%s ok=%s selected=%d",
            result.channel,
            result.ok,
            metrics.selected_count,
        )
        print(text)
        print(f"\n[send] channel={result.channel} ok={result.ok} detail={result.detail}")
        return 0 if result.ok else 4

    def _notify_fetch_failure(self, chat_id: str, detail: str) -> None:
        # 无 chat_id 时仍可经 webhook 通知（send_digest 会自动回退）。
        if self.dry_run or (not chat_id and not self.config.env.feishu_webhook_url):
            return
        try:
            sender = MessageSender(self.config, dry_run=False)
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "小麦 arXiv 晨报抓取失败"},
                    "template": "red",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"抓取失败：{detail}"}}
                ],
            }
            sender.send_digest(chat_id, card, f"晨报抓取失败：{detail}")
        except Exception as exc:  # noqa: BLE001
            logger.error("发送失败通知也失败：%s", exc)

    def _print_dry_run(
        self, reports: list[DigestReport], metrics: DigestMetrics, text: str
    ) -> None:
        print("=" * 70)
        print("DRY RUN — 不发送飞书")
        print("=" * 70)
        print(f"候选论文总数（展示窗口内）: {metrics.candidate_count}")
        print(f"入选论文总数: {metrics.selected_count}")
        for report in reports:
            print(
                f"  - [{report.profile_id}] {report.profile_name}: "
                f"入选 {len(report.items)} 篇"
            )
            ranked = self.debug_ranked.get(report.profile_id, [])
            if ranked:
                print("      top 候选评分（[J]=经 LLM 判定 / [f]=fallback；final/sem/kw/label）：")
            for rp in ranked:
                mark = "J" if rp.score.llm_judged else "f"
                line = (
                    f"        [{mark}] {rp.paper.arxiv_id_base} "
                    f"final={rp.score.final_score:.3f} "
                    f"sem={rp.score.semantic_score:.3f} "
                    f"kw={rp.score.keyword_score:+.2f} "
                    f"label={rp.score.judge_label} | {rp.paper.title[:55]}"
                )
                print(line)
                if rp.score.llm_judged and rp.score.judge_reason:
                    print(f"             理由: {rp.score.judge_reason[:90]}")
        if not self.ranker.semantic_available:
            print(
                "\n[模式] 未配置 embedding，采用 LLM-judge 主导筛选：关键词预筛 + LLM 相关性判断打分，"
                "semantic 分记为 0。配置 LLM_EMBEDDING_MODEL（或独立 embedding 提供商）可启用语义向量层。"
            )
        if not metrics.llm_completed and metrics.selected_count:
            print("\n[注意] 未配置 LLM 或调用失败，部分摘要为 fallback（未完成 LLM 总结）。")
        print("\n" + "-" * 70)
        print("渲染后的晨报文本：\n")
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="arXiv 飞书晨报作业")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="不发送飞书，仅打印")
    group.add_argument("--send", action="store_true", help="生成并发送晨报")
    parser.add_argument("--profile", default=None, help="只跑指定 profile id")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    dry_run = not args.send  # 默认 dry-run，除非显式 --send
    job = DailyDigest(dry_run=dry_run)
    return job.run(profile_id=args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
