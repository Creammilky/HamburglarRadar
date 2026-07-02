CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id TEXT NOT NULL,
    arxiv_id_base TEXT NOT NULL,
    version INTEGER,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL,
    authors_json TEXT NOT NULL,
    categories_json TEXT NOT NULL,
    primary_category TEXT,
    published_at TEXT,
    updated_at TEXT,
    abs_url TEXT NOT NULL,
    pdf_url TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(arxiv_id)
);

CREATE TABLE IF NOT EXISTS paper_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    keyword_score REAL NOT NULL,
    semantic_score REAL NOT NULL,
    llm_relevance_score REAL,
    final_score REAL NOT NULL,
    judge_label TEXT,
    judge_reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    profile_id TEXT,
    summary_type TEXT NOT NULL,
    language TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS digest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    candidate_count INTEGER DEFAULT 0,
    selected_count INTEGER DEFAULT 0,
    delivered_chat_id TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS delivered_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id_base TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    digest_run_id TEXT,
    UNIQUE(arxiv_id_base, profile_id, chat_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id_base TEXT NOT NULL,
    profile_id TEXT,
    user_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    feedback_text TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    user_id TEXT,
    chat_id TEXT,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    tool_args_json TEXT,
    tool_result_summary TEXT,
    risk_level TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_base ON papers(arxiv_id_base);
CREATE INDEX IF NOT EXISTS idx_scores_paper ON paper_scores(paper_id);
CREATE INDEX IF NOT EXISTS idx_feedback_base ON feedback(arxiv_id_base, profile_id);
