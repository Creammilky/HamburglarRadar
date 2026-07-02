# arxiv-feishu-research-agent

可长期运行的飞书研究助理：每天新加坡时间 08:30 自动汇总过去 24 小时内 arXiv 新发表、且与用户研究方向匹配的论文，生成中文晨报并发送到指定飞书群。用户还可在群里 `@` 机器人执行即时调研命令。

系统支持语义匹配、arXiv 分类匹配、关键词匹配、去重、相关性评分、中文摘要、反馈记录与审计日志，并默认最小权限、最小写操作。

## 当前进度

- **Milestone 1（已完成）**：本地 arXiv 晨报 dry-run。arXiv client、SQLite schema、关键词筛选、embedding 排序、LLM 摘要、`daily_digest_job --dry-run`。
- **Milestone 2（已完成）**：飞书发送。Hermes send adapter + Feishu webhook fallback、card_renderer、`daily_digest_job --send`。
- **Milestone 3（已完成）**：飞书群 `@` 交互。事件解析、command_parser、权限校验、会话、router，支持：今日论文 / 搜索主题 / 总结论文 / 撞车检查 / 反馈 / 保存 / 帮助。`python -m src.main serve` 或本地 `python -m src.main ask "..."`。
- Milestone 4~5（未开始）：Lark CLI 论文库写入（飞书多维表格）、反馈学习模型。

## Docker 部署（迁移友好）

```bash
cp .env.example .env          # 填入密钥
docker compose up -d --build  # agent=群聊长连接；scheduler=每日 08:30 晨报
docker compose logs -f agent
```

- SQLite / 缓存 / 本地 embedding 模型持久化在 `./data`（挂载卷），迁移时连同 `.env` 一起带走即可。
- 只跑群交互、不要定时器时，可删掉 compose 里的 `scheduler` 服务。
- 单独手动发一次晨报：`docker compose run --rm agent python -m src.scheduler.daily_digest_job --send`

## 群聊交互（Milestone 3）

### 方式 A（推荐）：飞书自建应用 + 长连接（本机可用，无需公网）

1. 在 [open.feishu.cn](https://open.feishu.cn) 创建**企业自建应用**，添加**机器人**能力。·
2. 权限：`im:message`、`im:message:send_as_bot`（发消息）、`im:chat`（群信息）。
3. 事件订阅选**长连接**，订阅 `im.message.receive_v1`；发布应用并把机器人拉进群。
4. `.env` 填 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，启动：

```bash
·python -m src.main serve --mode ws
```

之后在群里 `@机器人 今日论文` / `总结 <arXiv链接>` 即可。回复通过 App 消息 API 发到原会话；
耗时命令会先回一条“处理中”回执；`今日论文` 命中当天缓存则秒回（否则重建并缓存）。

### 方式 B：事件回调（需公网 HTTPS URL）

把事件订阅设为“发送到开发者服务器”，URL 指向本服务：

```bash
python -m src.main serve --mode http --port 9000   # POST /hermes/events，自动处理 challenge
```

无需 Hermes/飞书接入，本地模拟一条群命令：

```bash
python -m src.main ask "总结 https://arxiv.org/abs/2607.01084"
python -m src.main ask "查一下 cyber range LLM agent"
python -m src.main ask --send "今日论文"    # 实际推送回复到飞书
```

支持的命令：`今日论文` / `查一下 <主题>` / `总结 <arXiv链接>` / `这篇撞车吗 <链接>` / `保存到论文库 <链接>` / `不相关|必读|有用`（反馈）/ `帮助`。群聊必须 `@` 机器人，且仅 `allowed_chat_ids` / `allowed_user_ids` 可用。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env      # 按需填入 LLM / Feishu 配置（不填也能离线跑 dry-run）
python -m src.storage.db init
```

### Dry run（不发送飞书，仅打印晨报）

```bash
python -m src.scheduler.daily_digest_job --dry-run
```

可选参数：

```bash
python -m src.scheduler.daily_digest_job --dry-run --profile llm_security_range
```

### 发送晨报

```bash
python -m src.scheduler.daily_digest_job --send
python -m src.scheduler.daily_digest_job --send --profile llm_security_range
```

### 定时任务与时间设置

在 `config/app.yml` 里设：

```yaml
app:
  timezone: "Asia/Singapore"   # IANA 时区，用于换算当地时间
  daily_digest_time: "0830"    # 支持 "HH:MM" 或四位 "HHMM"
```

内置 APScheduler（按上面配置，自动换算时区）：

```bash
python -m src.scheduler.cron
```

或系统 cron（新加坡 08:30 == 00:30 UTC）：

```cron
30 0 * * * cd /path/to/HamburglarRadar && .venv/bin/python -m src.scheduler.daily_digest_job --send
```

#### 授时来源（应对服务器时钟不准）

默认用系统时钟。可在 `.env` 切换为联网授时来校正**晨报时间窗口**：

```ini
TIME_SOURCE=http     # system(默认) | http(读HTTPS的Date头，无需额外依赖) | ntp(需 pip install ntplib)
NTP_SERVER=pool.ntp.org
TIME_HTTP_URL=https://www.cloudflare.com
```

> 说明：APScheduler 的触发时刻仍依系统时钟，联网授时用于校正“过去 24 小时”窗口的计算。若要触发时刻也精确，建议在服务器启用 NTP 守护进程（`chrony` / `systemd-timesyncd`）。

## LLM 与 Embedding 后端

**Chat（judge + 摘要）**：配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_CHAT_MODEL`（OpenAI 兼容，如 DeepSeek）即启用真实 LLM 相关性判断与中文摘要；未配置则走文本 fallback（标记“未完成 LLM 总结”）。

**Embedding（语义相似度）三级后端**，按优先级自动选择：

1. 云端 API：配置 `LLM_EMBEDDING_MODEL`（需 endpoint 提供 `/embeddings`）。
2. 本地 `fastembed`：设置 `LOCAL_EMBEDDING_MODEL`（如 `BAAI/bge-small-en-v1.5`，首次下载 ~130MB，之后离线）。**DeepSeek 无 embedding 接口时推荐此项。**
3. 确定性哈希 fallback：以上都没有时启用，保证离线可跑（语义分偏弱）。

**筛选模式**：

- 有 embedding（云端或本地）→ 语义模式：keyword+semantic 组合预筛 top-N 送 LLM judge。
- 无任何 embedding → judge-primary 模式：keyword 预筛 + LLM judge 主导打分。
- 仅 LLM judge 真正判过的论文才允许入选，避免 fallback 误判。`ranking.judge_budget` 控制每个 profile 的 LLM 调用上限（默认 25）。

> 语义阈值（`config/app.yml` 的 `ranking`）已按 `bge-small-en-v1.5` 校准（该模型 cosine 普遍偏高）。换 embedding 模型时需相应调整。

arXiv 抓取需要外网访问 `export.arxiv.org`。

## 安全

- 群聊必须 `@` 机器人；仅 `allowed_chat_ids` / `allowed_user_ids` 可用。
- 所有写操作进入 audit log；Lark CLI 仅通过 allowlist wrapper 调用，禁止 `shell=True`。
- secret 不入库、不入日志、不回显到飞书。

## 测试

```bash
pytest
```

