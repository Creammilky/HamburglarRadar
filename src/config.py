"""配置加载：合并环境变量 (.env) 与 YAML 配置 (app.yml / profiles.yml)。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.models import ResearchProfile

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"


class AppSettings(BaseModel):
    name: str = "arxiv-feishu-research-agent"
    timezone: str = "Asia/Singapore"
    daily_digest_time: str = "08:30"
    digest_lookback_hours: int = 24

    def digest_hh_mm(self) -> tuple[int, int]:
        """解析晨报时间，支持 'HH:MM'、四位 'HHMM'、三位 'HMM'。"""
        s = str(self.daily_digest_time).strip().replace("：", ":")
        if ":" in s:
            parts = s.split(":")
            hh, mm = parts[0], (parts[1] if len(parts) > 1 else "0")
        elif s.isdigit() and len(s) == 4:
            hh, mm = s[:2], s[2:]
        elif s.isdigit() and len(s) == 3:
            hh, mm = s[:1], s[1:]
        else:
            hh, mm = (s or "8"), "0"
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"非法的 daily_digest_time: {self.daily_digest_time}")
        return hour, minute
    fetch_lookback_hours: int = 30
    max_papers_per_profile: int = 8
    max_total_papers_per_digest: int = 20
    language: str = "zh-CN"


class GatewaySettings(BaseModel):
    mode: str = "hermes"
    group_reply_requires_mention: bool = True
    allowed_chat_ids: list[str] = []
    allowed_user_ids: list[str] = []


class DefaultOutput(BaseModel):
    send_group_card: bool = True
    save_digest_to_doc: bool = False
    save_selected_to_base: bool = True


class FeishuSettings(BaseModel):
    sender: str = "hermes"
    lark_cli_enabled: bool = True
    default_output: DefaultOutput = Field(default_factory=DefaultOutput)


class RankingSettings(BaseModel):
    keyword_weight: float = 0.15
    semantic_weight: float = 0.55
    llm_judge_weight: float = 0.30
    min_final_score: float = 0.62
    min_semantic_score: float = 0.30
    semantic_reject_below: float = 0.25
    semantic_low_below: float = 0.35
    semantic_medium_below: float = 0.48
    # 无 embedding 时的 judge-primary 模式：语义权重重分配给 keyword 与 LLM judge
    judge_primary_keyword_weight: float = 0.25
    judge_primary_judge_weight: float = 0.75
    # 每个 profile 送 LLM judge 的最大候选数（两种模式共用，控制成本）
    judge_budget: int = 25


class SummarySettings(BaseModel):
    abstract_only_by_default: bool = True
    pdf_deep_summary_threshold: float = 0.78
    max_summary_tokens_per_paper: int = 700


class SafetySettings(BaseModel):
    dry_run: bool = False
    require_confirmation_for: list[str] = []
    shell_command_allowlist: list[str] = ["lark"]
    block_raw_shell: bool = True
    audit_all_tool_calls: bool = True


class EnvSettings(BaseModel):
    """运行时从环境变量读取的设置（secret 只驻留内存，不落库/日志）。"""

    app_env: str = "local"
    tz: str = "Asia/Singapore"
    log_level: str = "INFO"

    # 授时来源：system=系统时钟；http=读 HTTPS 响应的 Date 头；ntp=NTP 服务器
    time_source: str = "system"
    ntp_server: str = "pool.ntp.org"
    time_http_url: str = "https://www.cloudflare.com"

    hermes_base_url: str = "http://127.0.0.1:18789"
    hermes_agent_name: str = "arxiv-research-agent"
    feishu_home_chat_id: str = ""
    feishu_allowed_chat_ids: list[str] = []
    feishu_allowed_user_ids: list[str] = []
    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""
    # 自定义机器人若开启「自定义关键词」安全设置，消息内容须包含该关键词
    feishu_webhook_keyword: str = ""
    # 飞书自建应用（长连接接入群聊 @ 交互）
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    lark_cli_bin: str = "lark"
    lark_cli_timeout_seconds: int = 20
    lark_cli_dry_run: bool = False

    llm_provider: str = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_chat_model: str = ""
    llm_embedding_model: str = ""
    llm_timeout_seconds: int = 60
    # 本地 embedding 后端（fastembed），当云端无 embedding 时使用；留空则不启用
    local_embedding_model: str = ""

    arxiv_api_base: str = "https://export.arxiv.org/api/query"
    arxiv_request_delay_seconds: float = 3.0
    arxiv_max_results_per_profile: int = 200

    sqlite_path: str = "./data/research_agent.sqlite3"
    cache_dir: str = "./data/cache"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_chat_model)

    @property
    def embeddings_enabled(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_embedding_model)


class AppConfig(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)
    ranking: RankingSettings = Field(default_factory=RankingSettings)
    summary: SummarySettings = Field(default_factory=SummarySettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    env: EnvSettings = Field(default_factory=EnvSettings)
    profiles: list[ResearchProfile] = []


def _split_csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _load_env() -> EnvSettings:
    load_dotenv(REPO_ROOT / ".env")

    def _get(key: str, default: str = "") -> str:
        return os.getenv(key, default)

    return EnvSettings(
        app_env=_get("APP_ENV", "local"),
        tz=_get("TZ", "Asia/Singapore"),
        log_level=_get("LOG_LEVEL", "INFO"),
        time_source=_get("TIME_SOURCE", "system").lower(),
        ntp_server=_get("NTP_SERVER", "pool.ntp.org"),
        time_http_url=_get("TIME_HTTP_URL", "https://www.cloudflare.com"),
        hermes_base_url=_get("HERMES_BASE_URL", "http://127.0.0.1:18789"),
        hermes_agent_name=_get("HERMES_AGENT_NAME", "arxiv-research-agent"),
        feishu_home_chat_id=_get("FEISHU_HOME_CHAT_ID"),
        feishu_allowed_chat_ids=_split_csv(_get("FEISHU_ALLOWED_CHAT_IDS")),
        feishu_allowed_user_ids=_split_csv(_get("FEISHU_ALLOWED_USER_IDS")),
        feishu_webhook_url=_get("FEISHU_WEBHOOK_URL"),
        feishu_webhook_secret=_get("FEISHU_WEBHOOK_SECRET"),
        feishu_webhook_keyword=_get("FEISHU_WEBHOOK_KEYWORD"),
        feishu_app_id=_get("FEISHU_APP_ID"),
        feishu_app_secret=_get("FEISHU_APP_SECRET"),
        lark_cli_bin=_get("LARK_CLI_BIN", "lark"),
        lark_cli_timeout_seconds=int(_get("LARK_CLI_TIMEOUT_SECONDS", "20") or 20),
        lark_cli_dry_run=_get("LARK_CLI_DRY_RUN", "false").lower() == "true",
        llm_provider=_get("LLM_PROVIDER", "openai_compatible"),
        llm_base_url=_get("LLM_BASE_URL"),
        llm_api_key=_get("LLM_API_KEY"),
        llm_chat_model=_get("LLM_CHAT_MODEL"),
        llm_embedding_model=_get("LLM_EMBEDDING_MODEL"),
        llm_timeout_seconds=int(_get("LLM_TIMEOUT_SECONDS", "60") or 60),
        local_embedding_model=_get("LOCAL_EMBEDDING_MODEL"),
        arxiv_api_base=_get("ARXIV_API_BASE", "https://export.arxiv.org/api/query"),
        arxiv_request_delay_seconds=float(_get("ARXIV_REQUEST_DELAY_SECONDS", "3") or 3),
        arxiv_max_results_per_profile=int(_get("ARXIV_MAX_RESULTS_PER_PROFILE", "200") or 200),
        sqlite_path=_get("SQLITE_PATH", "./data/research_agent.sqlite3"),
        cache_dir=_get("CACHE_DIR", "./data/cache"),
    )


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(config_dir: Path = CONFIG_DIR) -> AppConfig:
    app_yml = _load_yaml(config_dir / "app.yml")
    profiles_yml = _load_yaml(config_dir / "profiles.yml")
    env = _load_env()

    profiles = [ResearchProfile(**p) for p in profiles_yml.get("profiles", [])]
    # semantic_query 使用 YAML 折叠标量，去掉多余换行/空白
    for p in profiles:
        p.semantic_query = " ".join(p.semantic_query.split())

    config = AppConfig(
        app=AppSettings(**app_yml.get("app", {})),
        gateway=GatewaySettings(**app_yml.get("gateway", {})),
        feishu=FeishuSettings(**app_yml.get("feishu", {})),
        ranking=RankingSettings(**app_yml.get("ranking", {})),
        summary=SummarySettings(**app_yml.get("summary", {})),
        safety=SafetySettings(**app_yml.get("safety", {})),
        env=env,
        profiles=profiles,
    )

    # 环境变量优先覆盖 gateway allowlist（若提供）
    if env.feishu_allowed_chat_ids:
        config.gateway.allowed_chat_ids = env.feishu_allowed_chat_ids
    if env.feishu_allowed_user_ids:
        config.gateway.allowed_user_ids = env.feishu_allowed_user_ids

    return config


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()


def load_prompt(name: str) -> str:
    """读取 config/prompts/<name>.md 模板。"""
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, **kwargs: str) -> str:
    """极简 {{var}} 模板渲染。"""
    out = template
    for key, value in kwargs.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out
