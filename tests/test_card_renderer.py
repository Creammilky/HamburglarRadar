from src.feishu import card_renderer
from src.models import (
    ArxivPaper,
    DigestItem,
    DigestReport,
    PaperScore,
    PaperSummary,
)


def _build_report():
    paper = ArxivPaper(
        arxiv_id="2607.01234v1",
        arxiv_id_base="2607.01234",
        version=1,
        title="LLM Agents for Cyber Range",
        abstract="An autonomous agent.",
        authors=["Alice"],
        categories=["cs.CR", "cs.AI"],
        primary_category="cs.CR",
        published_at="2026-07-01T12:00:00Z",
        abs_url="https://arxiv.org/abs/2607.01234",
        pdf_url="https://arxiv.org/pdf/2607.01234",
    )
    score = PaperScore(
        profile_id="llm_security_range",
        keyword_score=0.36,
        semantic_score=0.55,
        llm_relevance_score=0.8,
        final_score=0.72,
        judge_label="high",
        judge_reason="核心相关",
    )
    summary = PaperSummary(
        arxiv_id="2607.01234v1",
        profile_id="llm_security_range",
        title_zh="面向靶场的 LLM 智能体",
        one_sentence="用 LLM 智能体生成靶场。",
        problem="靶场生成成本高。",
        method="自主 LLM 智能体。",
        main_findings="有效。",
        why_relevant="与靶场生成方向直接相关。",
        limitations="摘要未说明。",
        collision_risk="medium",
        recommended_action="must_read",
    )
    item = DigestItem(paper=paper, score=score, summary=summary)
    return DigestReport(
        run_id="run-1",
        window_start="2026-07-01T00:30:00+00:00",
        window_end="2026-07-02T00:30:00+00:00",
        profile_name="LLM 安全靶场 / Agent 评测",
        profile_id="llm_security_range",
        items=[item],
    )


def test_render_text_contains_required_fields():
    text = card_renderer.render_text([_build_report()], "2026-07-02")
    assert "小麦 arXiv 晨报｜2026-07-02｜过去24小时" in text
    assert "必读：1 篇" in text
    assert "2607.01234" in text
    assert "Score: 0.72" in text
    assert "一句话结论：" in text
    assert "撞车风险：中" in text
    assert "建议动作：必读" in text
    assert "https://arxiv.org/abs/2607.01234" in text


def test_render_card_structure_complete():
    card = card_renderer.render_card([_build_report()], "2026-07-02")
    assert card["header"]["title"]["content"].startswith("小麦 arXiv 晨报")
    assert card["config"]["wide_screen_mode"] is True
    contents = " ".join(
        el.get("text", {}).get("content", "") for el in card["elements"]
    )
    assert "面向靶场的 LLM 智能体" in contents
    assert "必读" in contents
    assert "2607.01234" in contents


def test_render_text_empty_reports():
    empty = DigestReport(
        run_id="run-2",
        window_start="s",
        window_end="e",
        profile_name="X",
        profile_id="x",
        items=[],
    )
    text = card_renderer.render_text([empty], "2026-07-02")
    assert "没有筛到明显相关论文" in text


def test_compute_stats_counts_actions():
    stats = card_renderer.compute_stats([_build_report()])
    assert stats.total == 1
    assert stats.must_read == 1
    assert stats.skim == 0
