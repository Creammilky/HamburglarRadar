"""测试隔离：强制禁用外部 LLM / embedding / webhook，保证单测无网络副作用与费用。"""

import pytest

from src.config import get_config


@pytest.fixture(autouse=True, scope="session")
def _hermetic_env():
    # 全局会话存储改为纯内存，避免测试写真实 SQLite
    import src.agent.session as session_mod

    session_mod._STORE = session_mod.SessionStore(persist=False)

    env = get_config().env
    env.llm_api_key = ""
    env.llm_base_url = ""
    env.llm_chat_model = ""
    env.llm_embedding_model = ""
    env.local_embedding_model = ""
    env.feishu_webhook_url = ""
    env.feishu_app_id = ""
    env.feishu_app_secret = ""
    env.feishu_base_app_token = ""
    env.feishu_base_table_id = ""
    yield
