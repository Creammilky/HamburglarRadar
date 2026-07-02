"""Agent 工具集与注册表。把现有能力封装成 LLM 可调用的工具。

扩展方式（新增工具）：
    from src.agent.tools import Tool, build_default_registry
    reg = build_default_registry()
    reg.register(Tool(
        name="my_tool",
        description="做什么",
        parameters={"type": "object", "properties": {...}, "required": [...]},
        handler=lambda args, ctx: "结果字符串",
        writes=False,   # 若写外部资源置 True（会要求确认/审计）
    ))

安全：只读工具直接执行；写工具（保存到论文库）不直接写，而是登记一个待确认动作，
由用户回复“确认”后经既有安全通道（allowlist + 审计）落地。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.arxiv.client import ArxivClient
from src.arxiv.normalizer import split_arxiv_id
from src.config import AppConfig, load_prompt, render_prompt
from src.llm import LlmClient
from src.models import PaperScore, ResearchProfile
from src.observability.audit import AuditLogger
from src.observability.logger import get_logger
from src.relevance.embeddings import EmbeddingModel, cosine
from src.storage.repositories import FeedbackRepository
from src.summarizer.abstract_summary import AbstractSummarizer

logger = get_logger(__name__)


@dataclass
class ToolContext:
    config: AppConfig
    sessions: object  # SessionStore
    event: object  # HermesEvent
    arxiv: ArxivClient
    summarizer: AbstractSummarizer
    embeddings: EmbeddingModel
    llm: LlmClient
    conn_factory: Callable[[], object]  # () -> sqlite3.Connection

    def conv_id(self) -> str:
        return self.event.conversation_id()

    def profile(self) -> ResearchProfile:
        for p in self.config.profiles:
            if p.enabled:
                return p
        return ResearchProfile(
            id="default", name="用户研究方向", arxiv_categories=[], semantic_query="用户研究方向"
        )


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, ToolContext], str]
    writes: bool = False

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def specs(self) -> list[dict]:
        return [t.spec() for t in self.tools.values()]

    def execute(self, name: str, args: dict, ctx: ToolContext) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"[错误] 未知工具：{name}"
        try:
            return tool.handler(args or {}, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning("工具 %s 执行失败：%s", name, exc)
            return f"[工具 {name} 执行失败] {exc}"


# ---------------- 默认工具实现 ----------------

def _neutral_score(profile_id: str) -> PaperScore:
    return PaperScore(
        profile_id=profile_id, keyword_score=0.0, semantic_score=0.0,
        final_score=0.0, judge_label="medium", judge_reason="",
    )


def _tool_search(args: dict, ctx: ToolContext) -> str:
    topic = str(args.get("topic", "")).strip()
    if not topic:
        return "缺少 topic 参数"
    n = int(args.get("max_results", 8))
    papers = ctx.arxiv.search(f"all:{topic}", max_results=30)
    if not papers:
        return f"未找到与「{topic}」相关的近期论文。"
    vectors = ctx.embeddings.embed_texts([topic] + [f"{p.title}\n{p.abstract}" for p in papers])
    q = vectors[0]
    ranked = sorted(zip(papers, (cosine(q, v) for v in vectors[1:])), key=lambda x: x[1], reverse=True)
    lines = []
    for p, s in ranked[:n]:
        lines.append(f"- arXiv:{p.arxiv_id_base} | score {s:.2f} | {p.title}")
    return f"「{topic}」相关论文（按相关度）：\n" + "\n".join(lines)


def _tool_summarize(args: dict, ctx: ToolContext) -> str:
    aid = str(args.get("arxiv_id", "")).strip()
    if not aid:
        return "缺少 arxiv_id 参数"
    papers = ctx.arxiv.fetch_by_ids([aid])
    if not papers:
        return f"未找到 arXiv 论文：{aid}"
    paper = papers[0]
    profile = ctx.profile()
    summary = ctx.summarizer.summarize(paper, profile, _neutral_score(profile.id))
    ctx.sessions.set_last_paper(ctx.conv_id(), paper.arxiv_id_base)
    return json.dumps({
        "arxiv_id": paper.arxiv_id_base,
        "title": paper.title,
        "title_zh": summary.title_zh,
        "authors": paper.authors[:8],
        "one_sentence": summary.one_sentence,
        "problem": summary.problem,
        "method": summary.method,
        "main_findings": summary.main_findings,
        "why_relevant": summary.why_relevant,
        "collision_risk": summary.collision_risk,
        "limitations": summary.limitations,
        "recommended_action": summary.recommended_action,
        "abs_url": paper.abs_url,
    }, ensure_ascii=False)


def _tool_collision(args: dict, ctx: ToolContext) -> str:
    aid = str(args.get("arxiv_id", "")).strip()
    if not aid:
        return "缺少 arxiv_id 参数"
    papers = ctx.arxiv.fetch_by_ids([aid])
    if not papers:
        return f"未找到 arXiv 论文：{aid}"
    paper = papers[0]
    ctx.sessions.set_last_paper(ctx.conv_id(), paper.arxiv_id_base)
    if not ctx.llm.chat_enabled:
        return "撞车检查需要 LLM。"
    profile = ctx.profile()
    prompt = render_prompt(
        load_prompt("collision_check"),
        user_project_description=f"{profile.name}: {profile.semantic_query}",
        title=paper.title, abstract=paper.abstract,
    )
    data = ctx.llm.chat_json(system="你是严谨的科研撞车分析助手。", user=prompt)
    return json.dumps(data, ensure_ascii=False)


def _tool_daily_digest(args: dict, ctx: ToolContext) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.scheduler.daily_digest_job import DailyDigest, load_cached_digest

    today = datetime.now(ZoneInfo(ctx.config.app.timezone)).strftime("%Y-%m-%d")
    cached = load_cached_digest()
    if cached and cached.get("date") == today:
        return cached.get("text", "（今日晨报为空）")
    br = DailyDigest(dry_run=True).build()
    return br.text


def _tool_request_save(args: dict, ctx: ToolContext) -> str:
    aid = str(args.get("arxiv_id", "")).strip() or (
        ctx.sessions.get(ctx.conv_id()).last_arxiv_id_base or ""
    )
    if not aid:
        return "没有可保存的论文（请先指明 arXiv 链接或先总结一篇）。"
    base, _ = split_arxiv_id(aid)
    pending = ctx.sessions.get(ctx.conv_id()).pending_action
    if pending and pending.get("action") == "save_paper":
        ids = list(pending.get("arxiv_ids", []))
    else:
        ids = []
    if base not in ids:
        ids.append(base)
    ctx.sessions.set_pending(ctx.conv_id(), {"action": "save_paper", "arxiv_ids": ids})
    return (
        f"已登记待保存：{base}（当前共 {len(ids)} 篇待确认）。这是写操作，需要用户二次确认——"
        f"请在回复中提示用户发送「确认」以写入论文库。"
    )


def _tool_feedback(args: dict, ctx: ToolContext) -> str:
    aid = str(args.get("arxiv_id", "")).strip()
    ftype = str(args.get("feedback_type", "relevant")).strip()
    base = None
    if aid:
        base, _ = split_arxiv_id(aid)
    else:
        base = ctx.sessions.get(ctx.conv_id()).last_arxiv_id_base
    if not base:
        return "缺少论文引用（arxiv_id 或先处理过一篇）。"
    conn = ctx.conn_factory()
    FeedbackRepository(conn).add(
        arxiv_id_base=base, user_id=ctx.event.user_id, feedback_type=ftype,
        profile_id=ctx.profile().id, feedback_text=str(args.get("note", "")),
    )
    AuditLogger(conn).record(
        event_type="feedback", risk_level="low",
        user_id=ctx.event.user_id, chat_id=ctx.event.chat_id,
        tool_name="feedback.add", tool_args={"arxiv_id_base": base, "type": ftype},
    )
    return f"已记录对 {base} 的反馈：{ftype}。"


def build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(
        "search_arxiv", "按主题搜索近期 arXiv 论文，返回候选列表（含 arxiv_id 与相关度）。",
        {"type": "object",
         "properties": {"topic": {"type": "string", "description": "检索主题/关键词"},
                        "max_results": {"type": "integer", "description": "返回条数，默认8"}},
         "required": ["topic"]},
        _tool_search,
    ))
    reg.register(Tool(
        "summarize_paper", "根据 arXiv ID 生成中文结构化摘要（问题/方法/发现/相关性/局限）。",
        {"type": "object",
         "properties": {"arxiv_id": {"type": "string", "description": "arXiv ID 或带版本号"}},
         "required": ["arxiv_id"]},
        _tool_summarize,
    ))
    reg.register(Tool(
        "collision_check", "判断某篇论文是否与用户研究方向撞车（重叠/差异/建议）。",
        {"type": "object",
         "properties": {"arxiv_id": {"type": "string"}},
         "required": ["arxiv_id"]},
        _tool_collision,
    ))
    reg.register(Tool(
        "daily_digest", "获取今日 arXiv 中文晨报（过去24小时、按方向筛选）。",
        {"type": "object", "properties": {}},
        _tool_daily_digest,
    ))
    reg.register(Tool(
        "request_save_paper", "把某篇论文登记为待保存到论文库（写操作，需用户二次确认后生效）。",
        {"type": "object",
         "properties": {"arxiv_id": {"type": "string", "description": "留空则用会话中最近一篇"}}},
        _tool_request_save, writes=True,
    ))
    reg.register(Tool(
        "record_feedback", "记录用户对某篇论文的反馈（relevant/irrelevant/must_read/skip）。",
        {"type": "object",
         "properties": {"arxiv_id": {"type": "string"},
                        "feedback_type": {"type": "string",
                                          "enum": ["relevant", "irrelevant", "must_read", "skip"]}},
         "required": ["feedback_type"]},
        _tool_feedback, writes=True,
    ))
    return reg
