"""会话状态与多轮记忆，按飞书会话逻辑组织。

会话键 = HermesEvent.conversation_id()：
- 话题(thread)内回复：共享同一 thread_id → 记忆连续；
- 引用回复：root_id 串起线程；
- 群里直接 @：message_id 唯一 → 每条是新会话（清空上下文）。

每个会话保存：多轮历史（user/assistant 文本，条数裁剪）、最近论文指代、待确认动作。

稳健性（对应上下文管理改进 1/2/4）：
1. 持久化到 SQLite（conversation_state 表），容器重启不失忆、多进程可共享。
2. TTL：会话超期（默认 7 天）清理；pending 超期（默认 10 分钟）视为失效。
3. 线程安全：全局 RLock 串行化读写与 SQLite 访问；内存缓存 LRU 上限防膨胀。

测试可用 SessionStore(persist=False) 走纯内存。
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

_MAX_HISTORY_MESSAGES = 12          # 最多保留最近 N 条（约 6 轮）
_PENDING_TTL_SECONDS = 600          # 待确认动作 10 分钟过期
_CONV_TTL_DAYS = 7                  # 会话 7 天未活动清理
_CACHE_MAX = 500                    # 内存缓存最多会话数（LRU）


@dataclass
class ChatSession:
    conversation_id: str
    history: list[dict] = field(default_factory=list)  # [{"role","content"}]
    last_arxiv_id_base: Optional[str] = None
    pending_action: Optional[dict] = None
    pending_at: float = 0.0


class SessionStore:
    def __init__(self, persist: bool = True, sqlite_path: Optional[str] = None):
        self.persist = persist
        self.sqlite_path = sqlite_path
        self._cache: "OrderedDict[str, ChatSession]" = OrderedDict()
        self._lock = threading.RLock()
        self._conn = None
        self._ready = False

    # ---- 持久化底层 ----
    def _ensure_db(self):
        if not self.persist or self._ready:
            return
        from src.storage.db import get_connection, init_db

        init_db(self.sqlite_path)
        self._conn = get_connection(self.sqlite_path, check_same_thread=False)
        self._cleanup_expired()
        self._ready = True

    def _cleanup_expired(self):
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=_CONV_TTL_DAYS)).isoformat()
            self._conn.execute("DELETE FROM conversation_state WHERE updated_at < ?", (cutoff,))
            self._conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def _load(self, cid: str) -> Optional[ChatSession]:
        if not self.persist:
            return None
        self._ensure_db()
        row = self._conn.execute(
            "SELECT history_json, last_arxiv_id_base, pending_json, pending_at "
            "FROM conversation_state WHERE conversation_id = ?",
            (cid,),
        ).fetchone()
        if not row:
            return None
        pending = json.loads(row["pending_json"]) if row["pending_json"] else None
        try:
            pending_at = float(row["pending_at"]) if row["pending_at"] else 0.0
        except (TypeError, ValueError):
            pending_at = 0.0
        return ChatSession(
            conversation_id=cid,
            history=json.loads(row["history_json"] or "[]"),
            last_arxiv_id_base=row["last_arxiv_id_base"],
            pending_action=pending,
            pending_at=pending_at,
        )

    def _persist(self, s: ChatSession):
        if not self.persist:
            return
        self._ensure_db()
        self._conn.execute(
            "INSERT INTO conversation_state "
            "(conversation_id, history_json, last_arxiv_id_base, pending_json, pending_at, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET "
            "history_json=excluded.history_json, last_arxiv_id_base=excluded.last_arxiv_id_base, "
            "pending_json=excluded.pending_json, pending_at=excluded.pending_at, "
            "updated_at=excluded.updated_at",
            (
                s.conversation_id,
                json.dumps(s.history, ensure_ascii=False),
                s.last_arxiv_id_base,
                json.dumps(s.pending_action, ensure_ascii=False) if s.pending_action else None,
                str(s.pending_at or ""),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    # ---- 会话获取（内存缓存 + LRU） ----
    def get(self, conversation_id: str) -> ChatSession:
        with self._lock:
            if conversation_id in self._cache:
                self._cache.move_to_end(conversation_id)
                return self._cache[conversation_id]
            s = self._load(conversation_id) or ChatSession(conversation_id=conversation_id)
            self._cache[conversation_id] = s
            self._cache.move_to_end(conversation_id)
            if len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)
            return s

    # ---- 多轮历史 ----
    def get_history(self, conversation_id: str) -> list[dict]:
        with self._lock:
            return list(self.get(conversation_id).history)

    def append_history(self, conversation_id: str, role: str, content: str) -> None:
        with self._lock:
            s = self.get(conversation_id)
            s.history.append({"role": role, "content": content})
            if len(s.history) > _MAX_HISTORY_MESSAGES:
                s.history = s.history[-_MAX_HISTORY_MESSAGES:]
            self._persist(s)

    # ---- 论文指代 ----
    def set_last_paper(self, conversation_id: str, arxiv_id_base: str) -> None:
        with self._lock:
            s = self.get(conversation_id)
            s.last_arxiv_id_base = arxiv_id_base
            self._persist(s)

    # ---- 待确认动作（带 TTL） ----
    def set_pending(self, conversation_id: str, action: Optional[dict]) -> None:
        with self._lock:
            s = self.get(conversation_id)
            s.pending_action = action
            s.pending_at = time.time() if action else 0.0
            self._persist(s)

    def pop_pending(self, conversation_id: str) -> Optional[dict]:
        with self._lock:
            s = self.get(conversation_id)
            action, at = s.pending_action, s.pending_at
            s.pending_action = None
            s.pending_at = 0.0
            self._persist(s)
            if action and at and (time.time() - at) > _PENDING_TTL_SECONDS:
                return None  # 过期失效
            return action


_STORE = SessionStore()


def get_store() -> SessionStore:
    return _STORE
