"""对话意图路由测试。

验证 detect_intent 在 LLM 可用时通过 Skill 描述判断，
LLM 不可用时降级为关键词匹配。
"""

from insight_engine.conversation.router import detect_intent


def test_detect_intent_for_daily_news_report():
    """日报意图应被检测出来（LLM 不可用时会走关键词兜底）。"""
    intent, reason = detect_intent("帮我生成今日新闻分析报告")
    assert intent == "daily_news_report"
    assert reason


def test_detect_intent_for_normal_chat():
    """普通消息不应触发日报 Skill。"""
    intent, reason = detect_intent("你好，介绍一下这个项目")
    assert intent == "chat"
    assert reason
