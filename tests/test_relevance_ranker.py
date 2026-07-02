from src.models import ArxivPaper, ResearchProfile
from src.relevance.keyword_filter import score_keywords
from src.relevance.ranker import RelevanceRanker
from src.relevance.feedback_model import adjustment_for


PROFILE = ResearchProfile(
    id="llm_security_range",
    name="LLM 安全靶场 / Agent 评测",
    arxiv_categories=["cs.CR", "cs.AI"],
    semantic_query=(
        "LLM agents for cybersecurity, cyber range generation, penetration testing "
        "agents, CTF benchmark, MITRE ATT&CK extraction, autonomous red teaming."
    ),
    positive_keywords=["cyber range", "penetration testing", "red teaming", "LLM agent"],
    negative_keywords=["blockchain trading", "cryptocurrency price", "vehicle routing"],
    min_final_score=0.3,
    top_k=8,
)


def _paper(arxiv_id, title, abstract, categories):
    base = arxiv_id.split("v")[0]
    return ArxivPaper(
        arxiv_id=arxiv_id,
        arxiv_id_base=base,
        version=1,
        title=title,
        abstract=abstract,
        authors=["A"],
        categories=categories,
        primary_category=categories[0],
        published_at="2026-07-01T12:00:00Z",
        abs_url=f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{base}",
    )


RELEVANT = _paper(
    "2607.00001v1",
    "LLM agent for cyber range generation and penetration testing",
    "We build an autonomous LLM agent for cyber range generation and penetration "
    "testing with red teaming and MITRE ATT&CK mapping.",
    ["cs.CR", "cs.AI"],
)

IRRELEVANT = _paper(
    "2607.00002v1",
    "Vehicle routing with blockchain trading",
    "This paper studies vehicle routing and cryptocurrency price prediction. "
    "Blockchain trading optimization for logistics.",
    ["cs.NI"],
)


def test_positive_keywords_increase_score():
    result = score_keywords(RELEVANT, PROFILE)
    assert result.keyword_score > 0
    assert "cyber range" in result.positive_hits


def test_negative_keywords_decrease_score_and_reject():
    result = score_keywords(IRRELEVANT, PROFILE)
    assert result.keyword_score < 0
    # 命中 blockchain trading + vehicle routing + cryptocurrency price >= 2
    assert result.reject_candidate is True


def test_ranker_orders_relevant_above_irrelevant():
    ranker = RelevanceRanker()
    ranked = ranker.rank(PROFILE, [IRRELEVANT, RELEVANT])
    ids = [rp.paper.arxiv_id_base for rp in ranked]
    # 相关论文应排在前面（或不相关论文因负关键词被淘汰）
    assert ids[0] == "2607.00001"
    top = ranked[0]
    assert top.score.final_score > 0


def test_ranker_stable_ordering():
    ranker = RelevanceRanker()
    first = [rp.paper.arxiv_id_base for rp in ranker.rank(PROFILE, [RELEVANT, IRRELEVANT])]
    second = [rp.paper.arxiv_id_base for rp in ranker.rank(PROFILE, [RELEVANT, IRRELEVANT])]
    assert first == second


def test_feedback_adjustment_values():
    assert adjustment_for(["must_read"]) == 0.08
    assert adjustment_for(["irrelevant"]) == -0.08
    assert adjustment_for(["must_read", "relevant"]) == 0.12
    assert adjustment_for(["skip"]) == -0.12
