"""交互命令路由与各 flow。对应 schema 第 13 节与 Milestone 3。

支持：今日论文、搜索主题、总结论文、撞车检查、反馈、保存、帮助。
只读 flow（搜索/总结/撞车）无写操作；反馈/保存写 SQLite 并记入 audit。
飞书多维表格写入（save 到 base）留待 Milestone 4。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.agent import permissions
from src.agent.command_parser import parse_command
from src.agent.session import get_store
from src.arxiv.client import ArxivClient, ArxivClientError
from src.arxiv.normalizer import split_arxiv_id
from src.config import AppConfig, get_config, load_prompt, render_prompt
from src.feishu import card_renderer
from src.feishu.hermes_adapter import HermesEvent
from src.feishu.message_sender import MessageSender
from src.llm import LlmClient, LlmError
from src.models import ArxivPaper, CommandIntent, PaperScore, ResearchProfile
from src.observability.audit import AuditLogger
from src.observability.logger import get_logger
from src.relevance.embeddings import cosine
from src.relevance.ranker import RelevanceRanker
from src.storage.db import get_connection, init_db
from src.storage.repositories import (
    FeedbackRepository,
    PaperRepository,
    SummaryRepository,
)
from src.summarizer.abstract_summary import AbstractSummarizer

logger = get_logger(__name__)

_HELP_TEXT = (
    "我是小麦 🌾 arXiv 研究助理，你可以 @我：\n"
    "• **今日论文** —— 查看今天的 arXiv 晨报\n"
    "• **查一下 <主题>** —— 搜索某个主题的近期论文\n"
    "• **总结 <arXiv链接>** —— 生成中文结构化摘要\n"
    "• **这篇撞车吗 <arXiv链接>** —— 判断是否与你的方向重叠\n"
    "• **把这篇保存到论文库 <arXiv链接>** —— 存入论文库\n"
    "• **这篇不相关 / 必读 / 有用** —— 反馈，用于调整打分"
)


@dataclass
class Reply:
    card: dict
    text: str = ""


class Router:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self.client = ArxivClient()
        self.summarizer = AbstractSummarizer()
        self.ranker = RelevanceRanker()
        self.llm = LlmClient()
        self.sessions = get_store()
        self.sender = MessageSender(self.config, dry_run=False)
        self._conn = None

    # ---- infra ----
    def _conn_lazy(self):
        if self._conn is None:
            init_db()
            self._conn = get_connection()
        return self._conn

    def _context_profile(self) -> ResearchProfile:
        for p in self.config.profiles:
            if p.enabled:
                return p
        return ResearchProfile(
            id="default", name="用户研究方向", arxiv_categories=[], semantic_query="用户研究方向"
        )

    @staticmethod
    def _neutral_score(profile_id: str) -> PaperScore:
        return PaperScore(
            profile_id=profile_id,
            keyword_score=0.0,
            semantic_score=0.0,
            final_score=0.0,
            judge_label="medium",
            judge_reason="",
        )

    def _fetch_one(self, arxiv_id: str) -> Optional[ArxivPaper]:
        papers = self.client.fetch_by_ids([arxiv_id])
        return papers[0] if papers else None

    # ---- 入口 ----
    def route(self, event: HermesEvent) -> Optional[Reply]:
        decision = permissions.evaluate(event, self.config)
        if not decision.should_respond:
            logger.info("忽略消息：%s", decision.reason)
            return None
        if not decision.allowed:
            return Reply(
                card_renderer.simple_card("小麦｜无权限", "你不在授权名单，无法使用小麦。", "red"),
                text="无权限",
            )

        intent = parse_command(event.text)
        logger.info("intent=%s ids=%s topic=%s", intent.intent, intent.arxiv_ids, intent.topic)

        try:
            return self._dispatch(intent, event)
        except ArxivClientError as exc:
            return Reply(card_renderer.simple_card("小麦｜arXiv 错误", f"arXiv 访问失败：{exc}", "red"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("处理命令失败")
            return Reply(card_renderer.simple_card("小麦｜出错了", f"处理失败：{exc}", "red"))

    def handle(self, event: HermesEvent) -> Optional[Reply]:
        """路由并发送回复。返回 Reply（None 表示忽略未回复）。"""
        reply = self.route(event)
        if reply is not None:
            self.sender.send_message(event.chat_id, reply.card, reply.text)
        return reply

    def _dispatch(self, intent: CommandIntent, event: HermesEvent) -> Reply:
        handlers = {
            "help": lambda: self._help(),
            "daily_digest_now": lambda: self._digest_now(),
            "summarize_paper": lambda: self._summarize(intent, event),
            "search_topic": lambda: self._search(intent),
            "collision_check": lambda: self._collision(intent, event),
            "feedback": lambda: self._feedback(intent, event),
            "save_paper": lambda: self._save(intent, event),
            "update_profile": lambda: self._update_profile(intent),
            "unknown": lambda: self._unknown(),
        }
        return handlers.get(intent.intent, self._unknown)()

    # ---- flows ----
    def _help(self) -> Reply:
        return Reply(card_renderer.simple_card("小麦｜使用帮助", _HELP_TEXT), text=_HELP_TEXT)

    def _digest_now(self) -> Reply:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from src.scheduler.daily_digest_job import DailyDigest, load_cached_digest

        # 若今天已生成过晨报（如早晨定时任务），直接返回缓存，避免重跑耗时流程。
        today = datetime.now(ZoneInfo(self.config.app.timezone)).strftime("%Y-%m-%d")
        cached = load_cached_digest()
        if cached and cached.get("date") == today and cached.get("card"):
            return Reply(cached["card"], text=cached.get("text", ""))

        br = DailyDigest(dry_run=True).build()
        return Reply(br.card, text=br.text)

    def _summarize(self, intent: CommandIntent, event: HermesEvent) -> Reply:
        if not intent.arxiv_ids:
            return self._unknown()
        aid = intent.arxiv_ids[0]
        paper = self._fetch_one(aid)
        if paper is None:
            return Reply(card_renderer.simple_card("小麦｜未找到", f"没找到 arXiv 论文：{aid}", "orange"))
        profile = self._context_profile()
        summary = self.summarizer.summarize(paper, profile, self._neutral_score(profile.id))
        self.sessions.set_last_paper(event.chat_id, paper.arxiv_id_base)
        return Reply(
            card_renderer.render_paper_card(paper, summary, "小麦｜论文总结"),
            text=summary.one_sentence,
        )

    def _search(self, intent: CommandIntent) -> Reply:
        topic = (intent.topic or "").strip()
        if not topic:
            return self._unknown()
        papers = self.client.search(f"all:{topic}", max_results=30)
        if not papers:
            return Reply(card_renderer.render_search_card(topic, []))

        # 交互搜索按 embedding 相似度快速排序（不逐篇调 LLM judge，控制延迟）。
        em = self.ranker.embedding_model
        vectors = em.embed_texts([topic] + [f"{p.title}\n{p.abstract}" for p in papers])
        query_vec = vectors[0]
        scored = sorted(
            zip(papers, (cosine(query_vec, v) for v in vectors[1:])),
            key=lambda x: x[1],
            reverse=True,
        )
        top = scored[:3]
        adhoc = ResearchProfile(
            id="adhoc", name=topic, arxiv_categories=[], semantic_query=topic
        )
        results = []
        for paper, sim in top:
            summary = self.summarizer.summarize(paper, adhoc, self._neutral_score("adhoc"))
            results.append((paper, summary, f"Score: {sim:.2f}"))
        return Reply(card_renderer.render_search_card(topic, results), text=f"搜索：{topic}")

    def _collision(self, intent: CommandIntent, event: HermesEvent) -> Reply:
        aid = intent.arxiv_ids[0] if intent.arxiv_ids else None
        if not aid and self.sessions.get(event.chat_id).last_arxiv_id_base:
            aid = self.sessions.get(event.chat_id).last_arxiv_id_base
        if not aid:
            return Reply(card_renderer.simple_card("小麦｜撞车检查", "请带上 arXiv 链接，或先让我总结一篇。", "orange"))
        paper = self._fetch_one(aid)
        if paper is None:
            return Reply(card_renderer.simple_card("小麦｜未找到", f"没找到 arXiv 论文：{aid}", "orange"))
        if not self.llm.chat_enabled:
            return Reply(card_renderer.simple_card("小麦｜撞车检查", "撞车检查需要配置 LLM chat。", "orange"))

        profile = self._context_profile()
        prompt = render_prompt(
            load_prompt("collision_check"),
            user_project_description=f"{profile.name}: {profile.semantic_query}",
            title=paper.title,
            abstract=paper.abstract,
        )
        try:
            data = self.llm.chat_json(system="你是严谨的科研撞车分析助手。", user=prompt)
        except (LlmError, Exception) as exc:  # noqa: BLE001
            return Reply(card_renderer.simple_card("小麦｜撞车检查", f"分析失败：{exc}", "red"))

        risk = card_renderer.COLLISION_ZH.get(str(data.get("collision_risk", "unknown")), "未知")
        body = "\n".join(
            [
                f"**{paper.title}**",
                f"**撞车风险：**{risk}",
                "**重叠点：**" + "；".join(data.get("overlap_points", []) or ["—"]),
                "**关键差异：**" + "；".join(data.get("difference_points", []) or ["—"]),
                "**优先阅读：**" + "；".join(data.get("what_to_read_first", []) or ["—"]),
                f"**建议：**{data.get('suggested_response', '—')}",
                f"[abs]({paper.abs_url})",
            ]
        )
        self.sessions.set_last_paper(event.chat_id, paper.arxiv_id_base)
        return Reply(card_renderer.simple_card("小麦｜撞车检查", body, "purple"), text=f"撞车风险：{risk}")

    def _feedback(self, intent: CommandIntent, event: HermesEvent) -> Reply:
        aid_base = None
        if intent.arxiv_ids:
            aid_base, _ = split_arxiv_id(intent.arxiv_ids[0])
        else:
            aid_base = self.sessions.get(event.chat_id).last_arxiv_id_base
        if not aid_base:
            return Reply(
                card_renderer.simple_card("小麦｜反馈", "请指明是哪篇（带 arXiv 链接，或先让我总结一篇）。", "orange")
            )
        profile = self._context_profile()
        conn = self._conn_lazy()
        FeedbackRepository(conn).add(
            arxiv_id_base=aid_base,
            user_id=event.user_id,
            feedback_type=intent.feedback_type or "relevant",
            profile_id=profile.id,
            feedback_text=intent.raw_text,
        )
        AuditLogger(conn).record(
            event_type="feedback",
            risk_level="low",
            user_id=event.user_id,
            chat_id=event.chat_id,
            tool_name="feedback.add",
            tool_args={"arxiv_id_base": aid_base, "type": intent.feedback_type},
        )
        return Reply(
            card_renderer.simple_card(
                "小麦｜反馈已记录",
                f"已记录对 `{aid_base}` 的反馈：**{intent.feedback_type}**，将用于调整同类论文打分。",
                "green",
            ),
            text="反馈已记录",
        )

    def _save(self, intent: CommandIntent, event: HermesEvent) -> Reply:
        aid = intent.arxiv_ids[0] if intent.arxiv_ids else self.sessions.get(event.chat_id).last_arxiv_id_base
        if not aid:
            return Reply(card_renderer.simple_card("小麦｜保存", "请带上 arXiv 链接，或先让我总结一篇。", "orange"))
        paper = self._fetch_one(aid)
        if paper is None:
            return Reply(card_renderer.simple_card("小麦｜未找到", f"没找到 arXiv 论文：{aid}", "orange"))
        profile = self._context_profile()
        summary = self.summarizer.summarize(paper, profile, self._neutral_score(profile.id))
        conn = self._conn_lazy()
        paper_id = PaperRepository(conn).upsert(paper)
        SummaryRepository(conn).insert(
            paper_id, summary, summary_type="abstract",
            language=self.config.app.language, model_name=self.summarizer.model_name,
        )
        AuditLogger(conn).record(
            event_type="save_paper",
            risk_level="medium",
            user_id=event.user_id,
            chat_id=event.chat_id,
            tool_name="storage.save_paper",
            tool_args={"arxiv_id_base": paper.arxiv_id_base},
        )
        self.sessions.set_last_paper(event.chat_id, paper.arxiv_id_base)
        return Reply(
            card_renderer.simple_card(
                "小麦｜已保存",
                f"已把 **{paper.title}** 存入本地论文库（SQLite）。\n"
                f"写入飞书多维表格将在 Milestone 4 接入（需二次确认）。",
                "green",
            ),
            text="已保存到本地论文库",
        )

    def _update_profile(self, intent: CommandIntent) -> Reply:
        topic = (intent.topic or "").strip() or "（未识别到具体关注点）"
        return Reply(
            card_renderer.simple_card(
                "小麦｜方向更新",
                f"已记录你的新关注点：**{topic}**。\n"
                f"自动更新 profile 关键词属后续里程碑；当前可手动编辑 `config/profiles.yml`。",
                "orange",
            ),
            text="已记录关注点",
        )

    def _unknown(self) -> Reply:
        body = (
            "没太理解你的指令。可以试试：\n"
            "• 今日论文\n• 查一下 cyber range LLM agent\n"
            "• 总结 https://arxiv.org/abs/xxxx.xxxxx\n• 这篇撞车吗 <arXiv链接>"
        )
        return Reply(card_renderer.simple_card("小麦｜请再说清楚一点", body, "orange"), text=body)
