---
last_updated: 2026-05-28
status: "✅ Implemented"
owner: coding-agent
---

# Feature: 对话意图路由

## 目标
页面聊天入口根据用户消息自动判断意图：普通闲聊直接回复，日报请求触发 full pipeline。

## 技术方案

### 涉及的模块
- conversation/router.py: 意图检测和消息处理

### 实现细节
1. LLM 优先：读取 SKILL.md 描述 → 判断用户请求是否匹配日报 Skill
2. 关键词兜底：LLM 不可用时用中文关键词匹配（"日报"、"新闻分析报告" 等）
3. 统一返回：`ConversationResponse` 含 intent、message、artifacts 路径

### 验收标准
- "生成今日新闻分析报告" → 触发日报 pipeline
- "你好" → chat 模式，不触发 pipeline
- LLM 不可用时降级为关键词匹配
