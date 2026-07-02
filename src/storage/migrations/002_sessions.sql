-- 会话状态持久化（多轮记忆、指代、待确认动作）。对应 Agent 上下文管理。
CREATE TABLE IF NOT EXISTS conversation_state (
    conversation_id TEXT PRIMARY KEY,
    history_json TEXT NOT NULL DEFAULT '[]',
    last_arxiv_id_base TEXT,
    pending_json TEXT,
    pending_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversation_state(updated_at);
