from src.models import ArxivPaper, DigestItem, PaperScore, PaperSummary
from src.scheduler.daily_digest_job import _digest_item_sort_key


def _item(action, collision, final, aid):
    paper = ArxivPaper(
        arxiv_id=aid, arxiv_id_base=aid, version=1, title="t", abstract="a",
        authors=["x"], categories=["cs.CR"], primary_category="cs.CR",
        abs_url="u", pdf_url="p",
    )
    score = PaperScore(
        profile_id="p", keyword_score=0, semantic_score=0, final_score=final,
        judge_label="high", judge_reason="",
    )
    summary = PaperSummary(
        arxiv_id=aid, one_sentence="", problem="", method="", main_findings="",
        why_relevant="", limitations="", collision_risk=collision,
        recommended_action=action,
    )
    return DigestItem(paper=paper, score=score, summary=summary)


def test_must_read_before_skim():
    items = [
        _item("skim", "low", 0.9, "a"),
        _item("must_read", "low", 0.6, "b"),
    ]
    items.sort(key=_digest_item_sort_key)
    assert [i.paper.arxiv_id_base for i in items] == ["b", "a"]  # 必读在前，即便分低


def test_high_collision_before_low_within_same_action():
    items = [
        _item("must_read", "low", 0.9, "a"),
        _item("must_read", "high", 0.7, "b"),
    ]
    items.sort(key=_digest_item_sort_key)
    assert [i.paper.arxiv_id_base for i in items] == ["b", "a"]  # 同为必读，高撞车在前


def test_score_tiebreak():
    items = [
        _item("skim", "medium", 0.5, "a"),
        _item("skim", "medium", 0.8, "b"),
    ]
    items.sort(key=_digest_item_sort_key)
    assert [i.paper.arxiv_id_base for i in items] == ["b", "a"]  # 同档按分数降序
