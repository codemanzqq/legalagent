"""
意图识别：区分「税法/劳动法等专业咨询」与闲聊；非专业则返回引导语不走检索。

使用 DashScope 快模型输出 JSON；失败时默认视为专业问题以免阻断服务。
"""

from __future__ import annotations

import json  # 解析模型返回的 JSON 片段
import logging  # 记录意图调用异常
import re  # 提取 JSON 子串
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from modules.core.config import get_settings
from modules.rag.dashscope_http import get_dashscope_async_client, get_dashscope_sync_client

logger = logging.getLogger(__name__)


@lru_cache
def _intent_llm() -> ChatOpenAI:
    """意图专用 ChatOpenAI：低温、短超时。"""
    s = get_settings()
    return ChatOpenAI(
        model=s.intent_model,
        temperature=0.0,
        api_key=s.dashscope_api_key,
        base_url=s.dashscope_base_url,
        timeout=30,
        http_client=get_dashscope_sync_client(),
        http_async_client=get_dashscope_async_client(),
    )


_JSON_FENCE = re.compile(r"\{[^{}]*\}")  # 宽松匹配单行 JSON 对象


async def is_professional_query(question: str) -> bool:
    """True：进入检索+RAG；False：仅友好引导。"""
    if not question.strip():  # 空问题不调用模型
        return False
    s = get_settings()
    if not s.dashscope_api_key:  # 无密钥无法调用意图模型，放行检索以免服务不可用
        logger.warning("DASHSCOPE_API_KEY 未配置，跳过意图识别，默认 professional=True")
        return True

    sys = SystemMessage(
        content=(
            "你是意图分类器。判断用户问题是否属于税法、劳动法、社会保险、"
            "企业与劳动者权利义务等「专业知识」咨询。"
            "仅输出 JSON：{\"professional\": true|false}，不要其他文字。"
        ),
    )
    human = HumanMessage(content=f"问题：{question.strip()}")  # 用户原问句送入分类器
    try:
        resp = await _intent_llm().ainvoke([sys, human])  # 意图分支固定用小模型降本
        text = str(resp.content)  # 期望正文含一段 JSON
        m = _JSON_FENCE.search(text)  # 从模型输出中提取 {...} 片段
        if not m:  # 未匹配到 JSON 则保守视为专业问题
            return True
        data = json.loads(m.group(0))  # 解析 professional 布尔值
        return bool(data.get("professional", True))  # 缺省 True：避免误判阻断检索
    except Exception as exc:  # noqa: BLE001 — 解析/网络失败时默认走专业链路
        logger.warning("intent classify failed: %s", exc)
        return True
