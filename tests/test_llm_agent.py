from src.agent.llm_agent import LlmAgent
from src.agent.tools import Tool, ToolRegistry, build_default_registry
from src.feishu.hermes_adapter import HermesEvent


def _event(text="test"):
    return HermesEvent(
        event_id="e", chat_id="c", user_id="u", message_id="m",
        text=text, is_group=True, is_mention=True, timestamp="",
    )


def test_default_registry_has_core_tools():
    reg = build_default_registry()
    names = set(reg.tools)
    assert {"search_arxiv", "summarize_paper", "collision_check",
            "daily_digest", "request_save_paper", "record_feedback"} <= names
    specs = reg.specs()
    assert all(s["type"] == "function" and "name" in s["function"] for s in specs)


def test_registry_execute_unknown_tool():
    reg = ToolRegistry()
    out = reg.execute("nope", {}, ctx=None)
    assert "未知工具" in out


def test_registry_extensible():
    reg = build_default_registry()
    reg.register(Tool("my_tool", "d", {"type": "object", "properties": {}}, lambda a, c: "ok"))
    assert "my_tool" in reg.tools
    assert reg.execute("my_tool", {}, ctx=None) == "ok"


def test_agent_tool_loop(monkeypatch):
    reg = ToolRegistry()
    reg.register(Tool(
        "echo", "echo back",
        {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        lambda args, ctx: f"echo:{args.get('x')}",
    ))
    agent = LlmAgent(registry=reg)

    seq = iter([
        {  # 第一步：调用 echo 工具
            "role": "assistant", "content": None,
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "echo", "arguments": '{"x": "hi"}'},
            }],
        },
        {"role": "assistant", "content": "最终答复：hi"},  # 第二步：给出最终答复
    ])
    monkeypatch.setattr(
        agent.llm, "chat_messages",
        lambda messages, tools=None, temperature=0.3: next(seq),
    )

    text, used = agent.run(_event("echo hi"))
    assert "最终答复" in text
    assert used == ["echo"]


def test_agent_direct_answer_without_tools(monkeypatch):
    agent = LlmAgent(registry=ToolRegistry())
    monkeypatch.setattr(
        agent.llm, "chat_messages",
        lambda messages, tools=None, temperature=0.3: {"role": "assistant", "content": "你好"},
    )
    text, used = agent.run(_event("在吗"))
    assert text == "你好"
    assert used == []
