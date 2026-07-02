from src.agent.command_parser import parse_command, strip_mention


def test_strip_mention():
    assert strip_mention("@小麦 今日论文") == "今日论文"
    assert strip_mention("@xiaomai hello") == "hello"


def test_daily_digest_now():
    assert parse_command("@小麦 今日论文").intent == "daily_digest_now"
    assert parse_command("@小麦 晨报").intent == "daily_digest_now"


def test_search_topic():
    c = parse_command("@小麦 查一下 cyber range LLM agent 最近有什么新论文")
    assert c.intent == "search_topic"
    assert "cyber range" in c.topic.lower()
    assert "llm agent" in c.topic.lower()


def test_summarize_paper():
    c = parse_command("@小麦 总结 https://arxiv.org/abs/2607.01234")
    assert c.intent == "summarize_paper"
    assert c.arxiv_ids == ["2607.01234"]


def test_summarize_bare_id():
    c = parse_command("@小麦 总结 2607.01234v2")
    assert c.intent == "summarize_paper"
    assert c.arxiv_ids == ["2607.01234v2"]


def test_collision_check():
    c = parse_command("@小麦 这篇和我的靶场生成方向撞车吗 https://arxiv.org/abs/2607.05678")
    assert c.intent == "collision_check"
    assert c.arxiv_ids == ["2607.05678"]


def test_save_paper():
    c = parse_command("@小麦 把这篇保存到论文库 https://arxiv.org/abs/2607.05678")
    assert c.intent == "save_paper"
    assert c.arxiv_ids == ["2607.05678"]


def test_feedback_irrelevant():
    c = parse_command("@小麦 今天这篇不相关")
    assert c.intent == "feedback"
    assert c.feedback_type == "irrelevant"


def test_feedback_must_read():
    c = parse_command("@小麦 这篇必读")
    assert c.intent == "feedback"
    assert c.feedback_type == "must_read"


def test_update_profile():
    c = parse_command("@小麦 以后多关注 MITRE ATT&CK extraction 和 cyber range generation")
    assert c.intent == "update_profile"


def test_help():
    assert parse_command("@小麦 帮助").intent == "help"
    assert parse_command("@小麦 你能做什么").intent == "help"


def test_unknown():
    assert parse_command("@小麦 你好呀在吗").intent == "unknown"
