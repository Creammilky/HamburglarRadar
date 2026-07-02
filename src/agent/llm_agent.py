"""LLM 工具调用 Agent harness。对应用户需求：真正的 Agent + 可扩展工具。

流程：系统提示 + 用户消息 → LLM（带 tools）→ 若返回 tool_calls 则执行工具并回灌结果 →
循环直到 LLM 给出最终中文答复。写操作（保存论文库）只登记待确认，由用户回复“确认”后经
既有安全通道落地（见 router._confirm）。
"""

from __future__ import annotations

import json
from typing import Optional

from src.agent.tools import ToolContext, ToolRegistry, build_default_registry
from src.arxiv.client import ArxivClient
from src.config import AppConfig, get_config
from src.feishu.hermes_adapter import HermesEvent
from src.llm import LlmClient
from src.observability.logger import get_logger
from src.relevance.embeddings import EmbeddingModel
from src.storage.db import get_connection, init_db
from src.summarizer.abstract_summary import AbstractSummarizer

logger = get_logger(__name__)

_MAX_STEPS = 6

_SYSTEM_PROMPT = (
    "你是“麦旋风”，一个 arXiv 科研助理 Agent，服务于中文用户，工作在飞书群里。\n"
    "你可以调用工具来完成任务：搜索论文、生成中文摘要、撞车检查、获取今日晨报、"
    "记录反馈、把论文登记待保存到论文库。\n"
    "规则：\n"
    "- 用简体中文、简洁专业地回复。\n"
    "- 只依据工具返回的真实信息作答，不要编造论文内容、数据或结论。\n"
    "- 需要论文时优先用 arxiv_id；用户只给论文名/主题时，先用 search_arxiv 找到 arxiv_id 再处理。\n"
    "- 保存到论文库是写操作：调用 request_save_paper 后，务必在回复里提示用户发送“确认”才会写入。\n"
    "- 多篇/多步任务可依次调用多个工具后汇总。信息不足时简要追问，不要臆测执行写操作。"
)


class LlmAgent:
    def __init__(self, config: Optional[AppConfig] = None, registry: Optional[ToolRegistry] = None):
        self.config = config or get_config()
        self.llm = LlmClient(self.config.env)
        self.registry = registry or build_default_registry()
        self.arxiv = ArxivClient()
        self.summarizer = AbstractSummarizer()
        self.embeddings = EmbeddingModel()
        self._conn = None
        from src.agent.session import get_store

        self.sessions = get_store()

    def _conn_factory(self):
        if self._conn is None:
            init_db()
            self._conn = get_connection()
        return self._conn

    def _context(self, event: HermesEvent) -> ToolContext:
        return ToolContext(
            config=self.config, sessions=self.sessions, event=event,
            arxiv=self.arxiv, summarizer=self.summarizer, embeddings=self.embeddings,
            llm=self.llm, conn_factory=self._conn_factory,
        )

    def run(self, event: HermesEvent) -> tuple[str, list[str]]:
        """返回 (最终中文答复, 调用过的工具名列表)。"""
        ctx = self._context(event)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": event.text},
        ]
        used_tools: list[str] = []
        specs = self.registry.specs()

        for _ in range(_MAX_STEPS):
            msg = self.llm.chat_messages(messages, tools=specs)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return (msg.get("content") or "（无内容）"), used_tools

            # 追加 assistant（含 tool_calls）以维持上下文
            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                used_tools.append(name)
                logger.info("Agent 调用工具 %s args=%s", name, args)
                result = self.registry.execute(name, args, ctx)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result[:6000],
                })

        # 达到步数上限，做一次收尾总结
        messages.append({"role": "user", "content": "请基于以上信息用中文给出最终答复。"})
        final = self.llm.chat_messages(messages)
        return (final.get("content") or "（已达到步数上限）"), used_tools
