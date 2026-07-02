"""集成测试（离线）：注入固定候选论文与语义分，验证 send 流程与去重。

对应 schema 第 21.2 节：SQLite 写入 digest_runs、delivered_items 去重、重复运行不重复推送。
"""

import sqlite3

import pytest

from src.config import get_config
from src.feishu.message_sender import SendResult
from src.models import ArxivPaper, ResearchProfile
from src.relevance.llm_judge import JudgeResult
from src.scheduler import daily_digest_job as ddj
from src.scheduler.daily_digest_job import DailyDigest


PROFILE = ResearchProfile(
    id="test_profile",
    name="测试方向",
    arxiv_categories=["cs.CR"],
    semantic_query="cyber range LLM agent penetration testing",
    positive_keywords=["cyber range", "penetration testing", "LLM agent"],
    negative_keywords=["blockchain trading", "vehicle routing"],
    min_final_score=0.5,
    top_k=8,
)

RELEVANT = ArxivPaper(
    arxiv_id="2607.11111v1",
    arxiv_id_base="2607.11111",
    version=1,
    title="LLM agent for cyber range and penetration testing",
    abstract="Autonomous LLM agent for cyber range generation and penetration testing.",
    authors=["A"],
    categories=["cs.CR"],
    primary_category="cs.CR",
    published_at=None,
    abs_url="https://arxiv.org/abs/2607.11111",
    pdf_url="https://arxiv.org/pdf/2607.11111",
)
IRRELEVANT = ArxivPaper(
    arxiv_id="2607.22222v1",
    arxiv_id_base="2607.22222",
    version=1,
    title="Vehicle routing with blockchain trading",
    abstract="Vehicle routing and blockchain trading optimization.",
    authors=["B"],
    categories=["cs.NI"],
    primary_category="cs.NI",
    published_at=None,
    abs_url="https://arxiv.org/abs/2607.22222",
    pdf_url="https://arxiv.org/pdf/2607.22222",
)


@pytest.fixture
def patched_config(tmp_path, monkeypatch):
    cfg = get_config()
    monkeypatch.setattr(cfg, "profiles", [PROFILE])
    monkeypatch.setattr(cfg.env, "sqlite_path", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(cfg.env, "feishu_home_chat_id", "oc_test_chat")
    # 隔离晨报缓存路径，避免测试写入真实 data/last_digest.json
    monkeypatch.setattr(ddj, "_DIGEST_CACHE", tmp_path / "last_digest.json")
    return cfg


def _make_job(monkeypatch):
    job = DailyDigest(dry_run=False)

    # 注入固定候选，绕过网络。
    monkeypatch.setattr(
        job.client, "fetch_window", lambda *a, **k: [RELEVANT, IRRELEVANT]
    )
    # 强制语义模式并注入语义分：相关论文高分，不相关低分。
    monkeypatch.setattr(job.ranker, "semantic_available", True)
    monkeypatch.setattr(
        job.ranker,
        "_semantic_scores",
        lambda profile, papers: [0.75 if p.arxiv_id_base == "2607.11111" else 0.1 for p in papers],
    )
    # 模拟真实 LLM judge（llm_completed=True），使入选逻辑可触发。
    monkeypatch.setattr(
        job.ranker.judge,
        "judge",
        lambda paper, profile, semantic_score: JudgeResult(
            label="high",
            relevance_score=0.85,
            reason="测试",
            matched_aspects=[],
            mismatch_aspects=[],
            collision_risk="low",
            llm_completed=True,
        ),
    )
    return job


def test_send_flow_persists_and_dedups(patched_config, monkeypatch):
    sent = {"count": 0}

    def fake_send(self, chat_id, card, text):
        sent["count"] += 1
        return SendResult(ok=True, channel="test", detail="sent")

    monkeypatch.setattr(ddj.MessageSender, "send_digest", fake_send)

    # 第一次运行：应选中相关论文并投递。
    job = _make_job(monkeypatch)
    rc = job.run(profile_id="test_profile")
    assert rc == 0

    conn = sqlite3.connect(patched_config.env.sqlite_path)
    conn.row_factory = sqlite3.Row
    runs = conn.execute("SELECT status, selected_count FROM digest_runs").fetchall()
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["selected_count"] == 1

    delivered = conn.execute(
        "SELECT arxiv_id_base FROM delivered_items"
    ).fetchall()
    assert [r["arxiv_id_base"] for r in delivered] == ["2607.11111"]
    assert sent["count"] == 1
    conn.close()

    # 第二次运行：同一篇已投递，应去重，selected=0。
    job2 = _make_job(monkeypatch)
    rc2 = job2.run(profile_id="test_profile")
    assert rc2 == 0

    conn = sqlite3.connect(patched_config.env.sqlite_path)
    conn.row_factory = sqlite3.Row
    runs = conn.execute(
        "SELECT selected_count FROM digest_runs ORDER BY id"
    ).fetchall()
    assert runs[-1]["selected_count"] == 0
    # delivered_items 仍只有 1 条，未重复
    cnt = conn.execute("SELECT count(*) FROM delivered_items").fetchone()[0]
    assert cnt == 1
    conn.close()
