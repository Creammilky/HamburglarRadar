"""飞书晨报渲染。对应 schema 第 12 节。

- 提供纯文本渲染（第一版）与飞书交互卡片 JSON。
- 卡片交互按钮（必读/不相关/深度总结…）留待 Milestone 3/5 接入回调。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import DigestItem, DigestReport

ACTION_ZH = {
    "must_read": "必读",
    "skim": "可扫",
    "archive": "存档",
    "skip": "跳过",
}
COLLISION_ZH = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
}
_CIRCLED = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


@dataclass
class DigestStats:
    total: int
    must_read: int
    skim: int
    archive: int


def compute_stats(reports: list[DigestReport]) -> DigestStats:
    must_read = skim = archive = total = 0
    for report in reports:
        for item in report.items:
            total += 1
            action = item.summary.recommended_action
            if action == "must_read":
                must_read += 1
            elif action == "skim":
                skim += 1
            elif action == "archive":
                archive += 1
    return DigestStats(total=total, must_read=must_read, skim=skim, archive=archive)


def format_authors(authors: list[str], max_shown: int = 3) -> str:
    """作者信息：展示前若干位作者，超出用“等”。"""
    if not authors:
        return "作者未提供"
    shown = "、".join(authors[:max_shown])
    if len(authors) > max_shown:
        shown += f" 等 {len(authors)} 人"
    return shown


def _title_line(idx: int, item: DigestItem) -> str:
    title_zh = item.summary.title_zh or "(中文标题未生成)"
    # 英文标题在前，中文标题在后
    return f"{idx}. {item.paper.title} / {title_zh}"


def _item_block_text(idx: int, item: DigestItem) -> str:
    p = item.paper
    s = item.score
    m = item.summary
    lines = [
        _title_line(idx, item),
        f"   作者：{format_authors(p.authors)}",
        f"   arXiv: {p.arxiv_id_base} | Score: {s.final_score:.2f} | "
        f"Category: {p.primary_category or (p.categories[0] if p.categories else '-')}",
        f"   一句话结论：{m.one_sentence}",
        f"   方法：{m.method}",
        f"   为什么值得看：{m.why_relevant}",
        f"   撞车风险：{COLLISION_ZH.get(m.collision_risk, '未知')}",
        f"   建议动作：{ACTION_ZH.get(m.recommended_action, m.recommended_action)}",
        f"   链接：{p.abs_url} / {p.pdf_url or '-'}",
    ]
    return "\n".join(lines)


def render_text(reports: list[DigestReport], date_str: str) -> str:
    stats = compute_stats(reports)
    header = f"小麦 arXiv 晨报｜{date_str}｜过去24小时"
    if stats.total == 0:
        return header + "\n\n过去 24 小时没有筛到明显相关论文。"

    lines = [
        header,
        "",
        f"今天筛到 {stats.total} 篇相关论文，其中：",
        f"- 必读：{stats.must_read} 篇",
        f"- 可扫：{stats.skim} 篇",
        f"- 存档：{stats.archive} 篇",
    ]
    non_empty = [r for r in reports if r.items]
    for order, report in enumerate(non_empty):
        cn = _CIRCLED[order] if order < len(_CIRCLED) else str(order + 1)
        lines.append("")
        lines.append(f"【方向{cn}：{report.profile_name}】")
        for idx, item in enumerate(report.items, start=1):
            lines.append(_item_block_text(idx, item))
    return "\n".join(lines)


def _item_block_md(idx: int, item: DigestItem) -> str:
    p = item.paper
    s = item.score
    m = item.summary
    category = p.primary_category or (p.categories[0] if p.categories else "-")
    return (
        f"**{idx}. {p.title}**\n"
        f"{item.summary.title_zh or '(中文标题未生成)'}\n"
        f"**作者：**{format_authors(p.authors)}\n"
        f"`arXiv: {p.arxiv_id_base}` | `Score: {s.final_score:.2f}` | `{category}`\n"
        f"**一句话结论：**{m.one_sentence}\n"
        f"**方法：**{m.method}\n"
        f"**为什么值得看：**{m.why_relevant}\n"
        f"**撞车风险：**{COLLISION_ZH.get(m.collision_risk, '未知')} | "
        f"**建议动作：**{ACTION_ZH.get(m.recommended_action, m.recommended_action)}\n"
        f"[abs]({p.abs_url}) · [pdf]({p.pdf_url or p.abs_url})"
    )


def render_card(reports: list[DigestReport], date_str: str) -> dict:
    """构造飞书 interactive 卡片 JSON。"""
    stats = compute_stats(reports)
    header_title = f"小麦 arXiv 晨报｜{date_str}｜过去24小时"

    elements: list[dict] = []
    if stats.total == 0:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "过去 24 小时没有筛到明显相关论文。",
                },
            }
        )
    else:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"今天筛到 **{stats.total}** 篇相关论文：\n"
                        f"必读 {stats.must_read} · 可扫 {stats.skim} · 存档 {stats.archive}"
                    ),
                },
            }
        )
        non_empty = [r for r in reports if r.items]
        for order, report in enumerate(non_empty):
            cn = _CIRCLED[order] if order < len(_CIRCLED) else str(order + 1)
            elements.append({"tag": "hr"})
            elements.append(
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**【方向{cn}：{report.profile_name}】**",
                    },
                }
            )
            for idx, item in enumerate(report.items, start=1):
                elements.append(
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": _item_block_md(idx, item)},
                    }
                )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": "blue",
        },
        "elements": elements,
    }


# ---- 交互命令回复卡片（Milestone 3） ----


def simple_card(header_title: str, body_md: str, template: str = "blue") -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": template,
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": body_md}}],
    }


def paper_summary_md(paper, summary, score_line: str | None = None) -> str:
    """单篇论文摘要的 markdown 正文（英文标题在前，中文在后，含作者）。"""
    cat = paper.primary_category or (paper.categories[0] if paper.categories else "-")
    meta = f"`arXiv: {paper.arxiv_id_base}` | `{cat}`"
    if score_line:
        meta += f" | {score_line}"
    parts = [
        f"**{paper.title}**",
        summary.title_zh or "",
        f"**作者：**{format_authors(paper.authors)}",
        meta,
        f"**一句话结论：**{summary.one_sentence}",
        f"**研究问题：**{summary.problem}",
        f"**方法：**{summary.method}",
        f"**主要发现：**{summary.main_findings}",
        f"**为什么相关：**{summary.why_relevant}",
        f"**撞车风险：**{COLLISION_ZH.get(summary.collision_risk, '未知')}"
        f" | **建议：**{ACTION_ZH.get(summary.recommended_action, summary.recommended_action)}",
        f"**局限：**{summary.limitations}",
        f"[abs]({paper.abs_url}) · [pdf]({paper.pdf_url or paper.abs_url})",
    ]
    return "\n".join(p for p in parts if p)


def render_paper_card(paper, summary, header_title: str = "小麦｜论文总结") -> dict:
    return simple_card(header_title, paper_summary_md(paper, summary), template="green")


def render_search_card(topic: str, results: list[tuple]) -> dict:
    """results: list of (paper, summary, score_line)。"""
    if not results:
        body = f"没有找到与「{topic}」明显相关的近期论文。"
        return simple_card(f"小麦｜搜索：{topic}", body, template="orange")
    elements: list[dict] = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**搜索：{topic}** ，找到 {len(results)} 篇："},
        }
    ]
    for i, (paper, summary, score_line) in enumerate(results, start=1):
        elements.append({"tag": "hr"})
        block = paper_summary_md(paper, summary, score_line)
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**{i}.** {block}"}}
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"小麦｜搜索：{topic}"},
            "template": "blue",
        },
        "elements": elements,
    }
