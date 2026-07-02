"""飞书群命令解析。对应 schema 第 13 节。

解析策略（明确命令优先）：
1. 去掉 @机器人 提及。
2. 若含 arXiv URL/ID → single-paper flow（总结 / 撞车 / 保存）。
3. 否则按关键词判断：今日论文 / 搜索 / 反馈 / 更新方向 / 帮助。
4. 无法判断 → unknown（返回澄清，不执行任何写操作）。
"""

from __future__ import annotations

import re

from src.arxiv.normalizer import extract_arxiv_id_from_url
from src.models import CommandIntent

_MENTION_RE = re.compile(r"@[\w\u4e00-\u9fff\-\.]+")
_ARXIV_URL_RE = re.compile(r"https?://arxiv\.org/(?:abs|pdf)/[^\s]+", re.IGNORECASE)
_ARXIV_ID_RE = re.compile(r"\b(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+/\d{7}(?:v\d+)?)\b", re.IGNORECASE)

# 反馈词 → feedback_type
_FEEDBACK_WORDS = [
    (("不相关", "没用", "无关"), "irrelevant"),
    (("必读", "很重要", "重点"), "must_read"),
    (("有用", "相关", "有帮助"), "relevant"),
    (("跳过", "忽略"), "skip"),
]

_DIGEST_WORDS = ("今日论文", "今天论文", "今日的论文", "晨报", "今天的论文")
_SUMMARY_WORDS = ("总结", "概括", "讲讲", "解释", "介绍这篇", "说说这篇")
_COLLISION_WORDS = ("撞车", "冲突", "是否有人做", "有人做过", "重复了吗", "和我", "撞不撞")
_SAVE_WORDS = ("保存", "加入论文库", "收藏", "存到", "存进")
_SEARCH_WORDS = ("查一下", "查查", "搜一下", "搜索", "搜", "最近", "有什么新论文", "有什么论文", "找一下", "查")
_UPDATE_WORDS = ("以后多关注", "多关注", "增加关键词", "关注一下", "加个方向", "加关键词")
_HELP_WORDS = ("帮助", "help", "你能做什么", "怎么用", "使用说明", "指令")
_CONFIRM_WORDS = ("确认", "确定", "是的", "可以", "yes", "ok")


def strip_mention(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


def _extract_ids_and_urls(text: str) -> tuple[list[str], list[str]]:
    urls = _ARXIV_URL_RE.findall(text)
    ids: list[str] = []
    for u in urls:
        aid = extract_arxiv_id_from_url(u)
        if aid:
            ids.append(aid)
    # 裸 ID（排除已在 URL 中的）
    text_wo_urls = _ARXIV_URL_RE.sub(" ", text)
    for m in _ARXIV_ID_RE.findall(text_wo_urls):
        ids.append(m)
    # 去重保持顺序
    seen = set()
    uniq_ids = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq_ids.append(i)
    return uniq_ids, urls


def _contains(text: str, words) -> bool:
    return any(w.lower() in text.lower() for w in words)


def _extract_topic(text: str) -> str:
    topic = text
    for w in _SEARCH_WORDS:
        topic = topic.replace(w, " ")
    topic = re.sub(r"(有什么|最近|新论文|论文|的)", " ", topic)
    return " ".join(topic.split()).strip()


def parse_command(raw_text: str) -> CommandIntent:
    text = strip_mention(raw_text or "")
    ids, urls = _extract_ids_and_urls(text)

    def intent(name, **kw):
        return CommandIntent(
            intent=name, arxiv_ids=ids, urls=urls, raw_text=raw_text, **kw
        )

    # 帮助优先
    if _contains(text, _HELP_WORDS):
        return intent("help")

    # single-paper flow
    if ids:
        if _contains(text, _SAVE_WORDS):
            return intent("save_paper")
        if _contains(text, _COLLISION_WORDS):
            return intent("collision_check")
        # 默认对单篇进行总结
        return intent("summarize_paper")

    # 二次确认（简短且以确认词开头，指代上一条待确认动作，避免误判长句）
    if len(text) <= 6 and any(text.lower().startswith(w) for w in _CONFIRM_WORDS):
        return intent("confirm")

    # 无 ID 的保存/撞车：指代会话中最近处理过的一篇（router 用 session.last）
    if _contains(text, _SAVE_WORDS):
        return intent("save_paper")
    if _contains(text, _COLLISION_WORDS):
        return intent("collision_check")

    # 反馈（无 ID，指代上一篇）
    for words, ftype in _FEEDBACK_WORDS:
        if _contains(text, words):
            return intent("feedback", feedback_type=ftype)

    # 撞车但无 ID → 也走 feedback? 否则澄清；这里视为需要 ID 的 collision，返回 unknown
    if _contains(text, _DIGEST_WORDS):
        return intent("daily_digest_now")

    if _contains(text, _UPDATE_WORDS):
        topic = text
        for w in _UPDATE_WORDS:
            topic = topic.replace(w, " ")
        return intent("update_profile", topic=" ".join(topic.split()).strip())

    if _contains(text, _SUMMARY_WORDS) and not ids:
        # 想总结但没给链接
        return intent("unknown")

    if _contains(text, _SEARCH_WORDS):
        topic = _extract_topic(text)
        if topic:
            return intent("search_topic", topic=topic)

    return intent("unknown")
