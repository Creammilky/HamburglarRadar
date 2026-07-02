"""语义 embedding 与 cosine 相似度。对应 schema 第 10.2 节。

- 若配置了 LLM embedding，则调用真实 API。
- 否则使用确定性本地哈希 bag-of-words 向量作为 fallback，保证离线可跑且排序稳定。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Optional

from src.llm import LlmClient
from src.observability.logger import get_logger

logger = get_logger(__name__)

_FALLBACK_DIM = 512
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _hash_embed(text: str, dim: int = _FALLBACK_DIM) -> list[float]:
    """确定性 bag-of-words 哈希向量（L2 归一化）。"""
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall(text.lower())
    for tok in tokens:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class EmbeddingModel:
    """三级 embedding 后端：云端 API > 本地 fastembed > 确定性哈希 fallback。"""

    def __init__(self, client: Optional[LlmClient] = None):
        self.client = client or LlmClient()
        self._local = None
        self._local_ready = False

    def _get_local(self):
        """惰性初始化本地 fastembed；仅当配置了 local_embedding_model 时尝试。"""
        if self._local_ready:
            return self._local
        self._local_ready = True
        from src.config import get_config

        model_name = get_config().env.local_embedding_model
        if not model_name:
            self._local = None
            return None
        try:
            from fastembed import TextEmbedding

            self._local = TextEmbedding(model_name=model_name)
            logger.info("本地 embedding 后端就绪：%s", model_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("本地 embedding 不可用，回退哈希向量：%s", exc)
            self._local = None
        return self._local

    @property
    def uses_fallback(self) -> bool:
        """无真实语义能力（既无云端 API 也无本地模型）时为 True。"""
        if self.client.embeddings_enabled:
            return False
        return self._get_local() is None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.client.embeddings_enabled:
            try:
                return self.client.embed(texts)
            except Exception as exc:  # noqa: BLE001 - 回退本地
                logger.warning("embedding API failed, fallback: %s", exc)
        local = self._get_local()
        if local is not None:
            return [list(map(float, v)) for v in local.embed(list(texts))]
        return [_hash_embed(t) for t in texts]

    def similarity(self, query: str, doc: str) -> float:
        q_vec, d_vec = self.embed_texts([query, doc])
        return cosine(q_vec, d_vec)
