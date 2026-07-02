"""飞书多维表格（Bitable）论文库读写。对应 schema 第 18 节。

低层执行器：通过 tenant_access_token + REST 调用 Bitable（无 shell）。
业务去重/写入编排在 router，安全 allowlist 在 lark_cli。
"""

from __future__ import annotations

import time
from typing import Optional

import httpx

from src.config import AppConfig, get_config
from src.observability.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://open.feishu.cn/open-apis"

# 论文库字段（建表用）。type: 1=文本 2=数字 15=URL。首列为主键文本 arxiv_id。
PAPER_FIELD_SPEC: list[tuple[str, int]] = [
    ("arxiv_id", 1),
    ("title", 1),
    ("title_zh", 1),
    ("authors", 1),
    ("published_at", 1),
    ("categories", 1),
    ("profile", 1),
    ("score", 2),
    ("recommended_action", 1),
    ("collision_risk", 1),
    ("one_sentence", 1),
    ("why_relevant", 1),
    ("limitations", 1),
    ("abs_url", 1),
    ("pdf_url", 1),
    ("status", 1),
    ("notes", 1),
    ("created_at", 1),
]


def paper_to_fields(paper, summary, score, profile_name: str, status: str = "new") -> dict:
    """把论文/摘要/评分映射为多维表格字段。"""
    return {
        "arxiv_id": paper.arxiv_id_base,
        "title": paper.title,
        "title_zh": summary.title_zh or "",
        "authors": "、".join(paper.authors[:8]),
        "published_at": paper.published_at or "",
        "categories": ", ".join(paper.categories),
        "profile": profile_name,
        "score": float(round(score.final_score, 4)) if score else 0.0,
        "recommended_action": summary.recommended_action,
        "collision_risk": summary.collision_risk,
        "one_sentence": summary.one_sentence,
        "why_relevant": summary.why_relevant,
        "limitations": summary.limitations,
        "abs_url": paper.abs_url,
        "pdf_url": paper.pdf_url or paper.abs_url,
        "status": status,
        "notes": "",
    }


class FeishuBaseError(RuntimeError):
    pass


class FeishuBase:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self._token = ""
        self._token_exp = 0.0

    def _tenant_token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        env = self.config.env
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": env.feishu_app_id, "app_secret": env.feishu_app_secret},
            )
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") != 0:
            raise FeishuBaseError(f"获取 tenant_access_token 失败：{data.get('msg')}")
        self._token = data["tenant_access_token"]
        self._token_exp = time.time() + int(data.get("expire", 7200)) - 300
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._tenant_token()}"}

    def _req(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        url = f"{_BASE}{path}"
        with httpx.Client(timeout=20.0) as client:
            resp = client.request(method, url, headers=self._headers(), json=json_body)
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") != 0:
            raise FeishuBaseError(f"{path} 失败 code={data.get('code')} msg={data.get('msg')}")
        return data.get("data", {})

    def search_records(self, app_token: str, table_id: str, field: str, value: str) -> list[dict]:
        data = self._req(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search?page_size=20",
            {
                "filter": {
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": field, "operator": "is", "value": [value]}
                    ],
                }
            },
        )
        return data.get("items", []) or []

    def add_record(self, app_token: str, table_id: str, fields: dict) -> str:
        data = self._req(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            {"fields": fields},
        )
        return (data.get("record") or {}).get("record_id", "")

    def update_record(self, app_token: str, table_id: str, record_id: str, fields: dict) -> str:
        data = self._req(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            {"fields": fields},
        )
        return (data.get("record") or {}).get("record_id", record_id)

    def create_paper_table(self, app_token: str, name: str = "arXiv 论文库") -> str:
        """在已有多维表格 app 下创建论文库表（含全部字段），返回 table_id。建表助手。"""
        fields = [{"field_name": n, "type": t} for n, t in PAPER_FIELD_SPEC]
        data = self._req(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            {"table": {"name": name, "fields": fields}},
        )
        return data.get("table_id", "")
