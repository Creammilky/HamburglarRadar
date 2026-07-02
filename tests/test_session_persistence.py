from src.agent.session import SessionStore


def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "sess.sqlite3")
    s1 = SessionStore(persist=True, sqlite_path=path)
    s1.append_history("conv1", "user", "hi")
    s1.append_history("conv1", "assistant", "hello")
    s1.set_last_paper("conv1", "2607.01234")

    # 新实例读同一 DB，应恢复历史与指代（模拟容器重启）
    s2 = SessionStore(persist=True, sqlite_path=path)
    assert s2.get_history("conv1") == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert s2.get("conv1").last_arxiv_id_base == "2607.01234"


def test_pending_ttl_expired():
    s = SessionStore(persist=False)
    s.set_pending("c", {"action": "save_paper", "arxiv_ids": ["x"]})
    # 人为把登记时间设为很久以前 → pop 时判定过期
    s.get("c").pending_at = 1.0
    assert s.pop_pending("c") is None


def test_pending_fresh_returned():
    s = SessionStore(persist=False)
    s.set_pending("c", {"action": "save_paper", "arxiv_ids": ["x"]})
    action = s.pop_pending("c")
    assert action and action["arxiv_ids"] == ["x"]
    # 弹出后清空
    assert s.pop_pending("c") is None


def test_isolated_by_conversation():
    s = SessionStore(persist=False)
    s.append_history("A", "user", "aaa")
    s.append_history("B", "user", "bbb")
    assert s.get_history("A") == [{"role": "user", "content": "aaa"}]
    assert s.get_history("B") == [{"role": "user", "content": "bbb"}]
