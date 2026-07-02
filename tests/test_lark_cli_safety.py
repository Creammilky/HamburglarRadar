from src.feishu.lark_cli import ALLOWED_OPERATIONS, LarkCli, LarkCliCommand


def test_is_allowed_matches_allowlist():
    lark = LarkCli()
    assert lark.is_allowed(LarkCliCommand(domain="base", action="add_record"))
    assert lark.is_allowed(LarkCliCommand(domain="messenger", action="send_message"))
    assert not lark.is_allowed(LarkCliCommand(domain="base", action="drop_table"))


def test_run_rejects_non_allowlisted_action():
    lark = LarkCli()
    r = lark.run(LarkCliCommand(domain="base", action="delete_record", args={}))
    assert r.ok is False
    assert "不被允许" in r.error


def test_run_rejects_non_allowlisted_domain():
    lark = LarkCli()
    r = lark.run(LarkCliCommand(domain="tasks", action="create_task"))
    assert r.ok is False


def test_run_requires_confirmation_when_flagged():
    lark = LarkCli()
    cmd = LarkCliCommand(
        domain="base", action="add_record", require_confirmation=True, args={"fields": {}}
    )
    r = lark.run(cmd, confirmed=False)
    assert r.ok is False
    assert "确认" in r.error


def test_allowlisted_but_base_not_configured_is_safe_error():
    # 允许的操作、已确认，但未配置多维表格 → 安全报错，不抛异常、不联网
    lark = LarkCli()
    r = lark.run(
        LarkCliCommand(domain="base", action="add_record", args={"fields": {}}),
        confirmed=True,
    )
    assert r.ok is False
    assert "未配置" in r.error


def test_dangerous_operations_not_in_allowlist():
    # schema 第 14 节禁止：删文档/删记录/全量通讯录/大范围读群历史等
    for domain, action in [
        ("base", "delete_record"),
        ("docs", "delete_doc"),
        ("contact", "list_all_users"),
        ("messenger", "read_history"),
        ("shell", "run"),
    ]:
        assert action not in ALLOWED_OPERATIONS.get(domain, set())
