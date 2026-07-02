"""OpenAI 兼容 LLM 客户端封装：chat + embeddings。

设计要点：
- 当未配置 LLM（无 api_key/base_url/model）时，enabled=False，调用方应走本地 fallback。
- chat_json 输出严格 JSON，解析失败重试一次，仍失败抛出，由调用方 fallback。
- 不把 api_key 写入日志。
"""

from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from src.config import EnvSettings, get_config
from src.observability.logger import get_logger

logger = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class LlmError(RuntimeError):
    pass


class LlmClient:
    def __init__(self, env: Optional[EnvSettings] = None):
        self.env = env or get_config().env

    @property
    def chat_enabled(self) -> bool:
        return self.env.llm_enabled

    @property
    def embeddings_enabled(self) -> bool:
        return self.env.embeddings_enabled

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.env.llm_api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        if not self.chat_enabled:
            raise LlmError("LLM chat not configured")
        url = self.env.llm_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.env.llm_chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        with httpx.Client(timeout=self.env.llm_timeout_seconds) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    def chat_json(self, system: str, user: str) -> dict:
        """要求模型输出 JSON；解析失败重试一次。"""
        for attempt in range(2):
            content = self.chat(system, user)
            parsed = _extract_json(content)
            if parsed is not None:
                return parsed
            logger.warning("LLM JSON parse failed (attempt %d/2)", attempt + 1)
            user = user + "\n\n上一次输出不是合法 JSON，请只输出严格 JSON。"
        raise LlmError("LLM did not return valid JSON")

    def chat_messages(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.3,
    ) -> dict:
        """底层多轮对话，支持 OpenAI 兼容的 tools/tool_calls。返回 assistant message dict。"""
        if not self.chat_enabled:
            raise LlmError("LLM chat not configured")
        url = self.env.llm_base_url.rstrip("/") + "/chat/completions"
        payload: dict = {
            "model": self.env.llm_chat_model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        with httpx.Client(timeout=self.env.llm_timeout_seconds) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.embeddings_enabled:
            raise LlmError("LLM embeddings not configured")
        url = self.env.llm_base_url.rstrip("/") + "/embeddings"
        payload = {"model": self.env.llm_embedding_model, "input": texts}
        with httpx.Client(timeout=self.env.llm_timeout_seconds) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in data["data"]]


def _extract_json(content: str) -> Optional[dict]:
    content = content.strip()
    # 去掉可能的 ```json fences
    if content.startswith("```"):
        content = content.strip("`")
        content = re.sub(r"^json", "", content, flags=re.IGNORECASE).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(content)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None
