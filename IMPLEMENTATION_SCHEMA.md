# 实现说明（IMPLEMENTATION_SCHEMA）

本文件记录已落地的模块与里程碑状态，完整需求见项目根的 Schema 文档。

## 里程碑状态

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M1 | 本地 arXiv 晨报 dry-run | ✅ 已完成 |
| M2 | 飞书发送（Hermes / webhook fallback + 卡片） | ✅ 已完成 |
| M3 | 飞书群 @ 交互（解析/权限/会话/router + 各 flow） | ✅ 已完成 |
| M4 | 论文库写入飞书多维表格（lark_cli 安全 wrapper + base_writer + 二次确认 + 审计） | ✅ 已完成 |
| M5 | 反馈学习模型 | ⬜ 未开始（feedback 表、命令与 feedback_adjustment 已就绪） |

## 模块映射

- `src/config.py`：合并 `.env` 与 `config/*.yml`，输出 `AppConfig`；secret 仅驻留内存。
- `src/models.py`：Pydantic 领域模型（ResearchProfile / ArxivPaper / PaperScore / PaperSummary / DigestItem / DigestReport）。
- `src/llm.py`：OpenAI 兼容 chat + embeddings 封装；未配置时 `*_enabled=False`，调用方走本地 fallback。
- `src/arxiv/`：`query_builder`（UTC 时间窗 + 分类）、`client`（重试/退避/限速/Atom 解析）、`normalizer`（去版本、URL 提取）。
- `src/relevance/`：`keyword_filter`（正负关键词打分）、`embeddings`（真实 API 或确定性哈希 fallback + cosine）、`llm_judge`（JSON judge + 语义分 fallback）、`ranker`（三层融合 + 反馈调整 + 选择）、`feedback_model`（feedback_adjustment）。
- `src/summarizer/abstract_summary.py`：基于 title+abstract 的中文结构化摘要；无 LLM 时文本 fallback 并标记“未完成 LLM 总结”。
- `src/feishu/`：`card_renderer`（纯文本 + interactive 卡片）、`hermes_adapter`（事件解析、should_respond、send_text/card）、`message_sender`（Hermes 优先 → webhook fallback → 本地保存）。
- `src/storage/`：`migrations/001_init.sql`（全表）、`db`（连接/初始化）、`repositories`（各表读写）。
- `src/scheduler/`：`daily_digest_job`（`--dry-run`/`--send`/`--profile` 编排）、`cron`（APScheduler 08:30 SGT）。
- `src/observability/`：`logger`（含 secret 屏蔽）、`audit`（审计写入）、`metrics`。
- `src/agent/`（M3）：`command_parser`（意图解析）、`permissions`（@ + allowlist 判定）、`session`（chat 最近论文上下文）、`router`（各 flow：今日论文/搜索/总结/撞车/反馈/保存/帮助）、`serve`（HTTP 事件接收 + 飞书 challenge）、`cli`（本地模拟）。
- `src/main.py`：顶层 CLI（`digest` / `serve` / `ask`）。

## 关键设计决策

- **离线可跑**：未配置 LLM 时用确定性哈希 embedding 与文本摘要 fallback，保证 dry-run 无外网/无 key 也能运行；此时语义分偏低属正常，schema 阈值需按真实 embedding 校准。
- **时区**：本地按 `Asia/Singapore`，arXiv 查询统一转 UTC；抓取窗口 30h、展示窗口 24h。
- **去重**：候选按 `arxiv_id_base` 去重；投递按 `(arxiv_id_base, profile_id, chat_id)` 去重，重复运行不重复推送。
- **安全**：secret 不入库/日志/回显；发送失败保存到 `data/failed_digest_*.json`；Lark CLI/交互写操作留待 M3/M4，按 allowlist + 二次确认设计。

## 测试

- `tests/test_arxiv_query_builder.py`：时间窗、去版本、URL 提取、feed 解析。
- `tests/test_relevance_ranker.py`：正/负关键词打分、排序、稳定性、反馈调整。
- `tests/test_card_renderer.py`：晨报文本与卡片字段完整。
- `tests/test_daily_digest_integration.py`：send 流程持久化 + delivered 去重（离线注入）。
- `tests/test_command_parser.py`：M3 命令解析（今日论文/搜索/总结/撞车/保存/反馈/更新/帮助）。

`test_lark_cli_safety` 对应 M4，随 Lark CLI wrapper 补齐。
