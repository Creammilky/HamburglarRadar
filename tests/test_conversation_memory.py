from src.agent.llm_agent import LlmAgent
from src.agent.session import SessionStore
from src.agent.tools import ToolRegistry
from src.feishu.hermes_adapter import HermesEvent


def _ev(message_id="m", text="hi", thread_id="", root_id=""):
    return HermesEvent(
        event_id="e", chat_id="c", user_id="u", message_id=message_id,
        text=text, is_group=True, is_mention=True, timestamp="",
        thread_id=thread_id, root_id=root_id,
    )


def test_conversation_id_priority():
    assert _ev("m1", thread_id="T", root_id="R").conversation_id() == "T"
    assert _ev("m2", root_id="R2").conversation_id() == "R2"
    assert _ev("m3").conversation_id() == "m3"


def test_history_cap():
    s = SessionStore(persist=False)
    for i in range(30):
        s.append_history("c", "user", str(i))
    assert len(s.get_history("c")) <= 12


def _fresh_agent(monkeypatch, captured):
    agent = LlmAgent(registry=ToolRegistry())
    agent.sessions = SessionStore(persist=False)  # 隔离，避免全局单例污染
    monkeypatch.setattr(
        agent.llm, "chat_messages",
        lambda messages, tools=None, temperature=0.3: (
            captured.append(list(messages)) or {"role": "assistant", "content": "ok"}
        ),
    )
    return agent


def test_thread_reply_carries_history(monkeypatch):
    captured = []
    agent = _fresh_agent(monkeypatch, captured)
    # 首条（话题根）：conv = root1
    agent.run(_ev(message_id="root1", text="第一条"))
    # 话题内回复：thread_id=root1 → 同一会话
    agent.run(_ev(message_id="m2", thread_id="root1", text="第二条"))
    joined = str(captured[-1])
    assert "第一条" in joined and "第二条" in joined


def test_direct_at_is_new_conversation(monkeypatch):
    captured = []
    agent = _fresh_agent(monkeypatch, captured)
    agent.run(_ev(message_id="A", text="AAA"))
    agent.run(_ev(message_id="B", text="BBB"))  # 无 thread/root，新 message_id → 新会话
    assert "AAA" not in str(captured[-1])
    assert "BBB" in str(captured[-1])
