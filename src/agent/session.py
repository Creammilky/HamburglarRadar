"""极简会话状态：记录每个 chat 最近一次涉及的论文，供“这篇不相关/保存这篇”等指代使用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChatSession:
    chat_id: str
    last_arxiv_id_base: Optional[str] = None
    # 待二次确认的动作，如 {"action": "save_paper", "arxiv_id": "..."}
    pending_action: Optional[dict] = None


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, ChatSession] = {}

    def get(self, chat_id: str) -> ChatSession:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = ChatSession(chat_id=chat_id)
        return self._sessions[chat_id]

    def set_last_paper(self, chat_id: str, arxiv_id_base: str) -> None:
        self.get(chat_id).last_arxiv_id_base = arxiv_id_base

    def set_pending(self, chat_id: str, action: Optional[dict]) -> None:
        self.get(chat_id).pending_action = action

    def pop_pending(self, chat_id: str) -> Optional[dict]:
        s = self.get(chat_id)
        action = s.pending_action
        s.pending_action = None
        return action


# 进程内单例（第一版够用；多进程部署需换持久化存储）
_STORE = SessionStore()


def get_store() -> SessionStore:
    return _STORE
