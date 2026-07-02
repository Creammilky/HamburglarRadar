"""会话状态与多轮记忆，按飞书会话逻辑组织。

会话键 = HermesEvent.conversation_id()：
- 话题(thread)内回复：共享同一 thread_id → 记忆连续；
- 引用回复：root_id 串起线程；
- 群里直接 @：message_id 唯一 → 每条是新会话（清空上下文）。

每个会话保存：多轮对话历史（user/assistant 文本，带上限裁剪）、最近论文、待确认动作。
进程内存储（重启清空）；如需持久化可换 SQLite。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

_MAX_HISTORY_MESSAGES = 12  # 最多保留最近 N 条（约 6 轮）


@dataclass
class ChatSession:
    conversation_id: str
    history: list[dict] = field(default_factory=list)  # [{"role","content"}]
    last_arxiv_id_base: Optional[str] = None
    pending_action: Optional[dict] = None


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, ChatSession] = {}

    def get(self, conversation_id: str) -> ChatSession:
        if conversation_id not in self._sessions:
            self._sessions[conversation_id] = ChatSession(conversation_id=conversation_id)
        return self._sessions[conversation_id]

    # ---- 多轮历史 ----
    def get_history(self, conversation_id: str) -> list[dict]:
        return list(self.get(conversation_id).history)

    def append_history(self, conversation_id: str, role: str, content: str) -> None:
        s = self.get(conversation_id)
        s.history.append({"role": role, "content": content})
        if len(s.history) > _MAX_HISTORY_MESSAGES:
            s.history = s.history[-_MAX_HISTORY_MESSAGES:]

    # ---- 论文指代 ----
    def set_last_paper(self, conversation_id: str, arxiv_id_base: str) -> None:
        self.get(conversation_id).last_arxiv_id_base = arxiv_id_base

    # ---- 待确认动作 ----
    def set_pending(self, conversation_id: str, action: Optional[dict]) -> None:
        self.get(conversation_id).pending_action = action

    def pop_pending(self, conversation_id: str) -> Optional[dict]:
        s = self.get(conversation_id)
        action = s.pending_action
        s.pending_action = None
        return action


_STORE = SessionStore()


def get_store() -> SessionStore:
    return _STORE
