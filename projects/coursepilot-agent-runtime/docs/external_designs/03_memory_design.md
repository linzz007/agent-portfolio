# Memory 设计说明

## 1. 字段与存储对象

### 1.1 episodes：逐条历史事件

| 字段 | 含义 | 写入来源 | 使用方式 |
|---|---|---|---|
| id | 单条记忆的唯一 ID | 保存记忆时生成 | 检索结果引用、排查数据 |
| user_id | 用户维度，默认 default | 当前会话 | 隔离不同用户的学习历史 |
| course_name | 课程维度 | 前端请求或运行上下文 | 限制记忆只在同一课程内使用 |
| event_type | 事件类型：qa、mistake、practice、exam | 学习、练习、考试流程 | 检索时过滤不同类型记忆 |
| content | 记忆正文 | 问答、作答结果、评分反馈 | 进入后续提示词上下文 |
| importance | 重要度，默认 0.5 | 由场景决定 | 检索排序，错误类通常更高 |
| created_at | 写入时间 | 保存记忆时生成 | 最近记录排序、展示时间 |
| metadata | 扩展信息 JSON | 不同流程补充 | 存 score、tags、weak_points、doc_ids、mode、agent、phase 等 |

### 1.2 user_profiles：用户学习画像

| 字段 | 含义 | 更新时机 | 使用方式 |
|---|---|---|---|
| user_id + course_name | 联合主键 | 首次写入画像时创建 | 按用户和课程读取画像 |
| weak_points | 薄弱点列表 | 错题、考试弱点产生后更新 | 生成个性化学习提示 |
| concept_mastery | 概念掌握度映射 | 有 score 和 concepts 时更新 | 记录每个概念的 mastery、attempts、avg_score |
| pref_style | 学习偏好，默认 step_by_step | 画像写入时保留 | 可用于后续个性化表达 |
| total_qa | 学习问答次数 | 学习 QA 写入时递增 | 画像统计 |
| total_practice | 练习次数 | 练习评分写入时递增 | 画像统计 |
| avg_score | 平均分 | 练习评分写入时滚动更新 | 画像统计 |
| updated_at | 画像更新时间 | 每次画像 upsert | 判断画像新旧 |

### 1.3 检索索引

| 对象 | 内容 | 作用 | 降级方式 |
|---|---|---|---|
| episodes_fts | episodes 的 content 全文索引 | 优先用于关键词检索 | 如果当前 SQLite 不支持 FTS5，自动退回 LIKE 检索 |
| idx_ep_course | user_id、course_name、created_at | 按课程读取最近记忆 | 普通索引 |
| idx_ep_type | user_id、course_name、event_type | 按事件类型过滤 | 普通索引 |

### 1.4 memory_search 工具接口

| 字段 | 方向 | 是否必填 | 含义 |
|---|---|---|---|
| query | 入参 | 是 | 当前用户问题或作答内容 |
| course_name | 入参 | 是 | 限定课程范围 |
| event_types | 入参 | 否 | 限定 qa、mistake、practice、exam |
| mode | 入参 | 否 | learn、practice、exam |
| agent | 入参 | 否 | tutor、quizzer、grader |
| phase | 入参 | 否 | answer、generate、grade |
| top_k | 入参 | 否 | 最多返回条数 |
| results | 出参 | 是 | 命中的记忆列表 |
| summary | 出参 | 是 | 单条记忆的短摘要 |
| success | 出参 | 是 | 工具调用是否成功 |
| message/error | 出参 | 否 | 无结果或失败原因 |

## 2. 关键使用位置

| 使用位置 | 读/写 | 发生阶段 | 具体作用 |
|---|---|---|---|
| learn/tutor/answer 前 | 读 | 学习模式生成回答前 | 查找同课历史 QA、错题、练习和考试记录，补充给导师 Agent |
| practice/quizzer/generate 前 | 读 | 练习题生成前 | 让出题 Agent 参考历史薄弱点和练习记录 |
| practice/grader/grade 前 | 读 | 练习评分前 | 让评分 Agent 参考历史错误和同类题表现 |
| practice/grader/grade 后 | 写 | 评分完成后 | 写入 practice 或 mistake，并更新画像 |
| exam/quizzer/generate 前 | 读 | 考试题生成前 | 参考历史学习情况生成试题 |
| exam/grader/grade 前 | 读 | 考试评分前 | 参考历史弱点辅助评分反馈 |
| exam/grader/grade 后 | 写 | 考试评分完成后 | 写入 exam，并根据弱点更新画像 |
| learn/tutor/answer 后 | 写 | 流式学习回答完成后 | 写入 qa，记录本次学习问答 |
| ContextBudgeter | 读后组装 | LLM 调用前 | 将 memory_text 作为上下文一段拼入最终 prompt |
| Harness Artifact | 观测 | 运行完成后 | 收集 memory read/write/search 相关事件，供 trace 分析 |

## 3. 增删改查时机

| 操作 | 当前实现 | 触发时机 | 结果 |
|---|---|---|---|
| 增加 episode | 已实现 | 学习 QA、练习评分、考试评分后 | 写入 episodes 表 |
| 查询 episode | 已实现 | 各模式调用 Agent 前 | 通过 FTS5 或 LIKE 检索相关历史 |
| 修改 episode | 未作为业务能力暴露 | 当前流程不修改历史事件正文 | 历史事件保持追加式记录 |
| 删除 episode | 未作为业务能力暴露 | 当前流程不主动删除记忆 | 如果未来物理删除，FTS 触发器会同步索引 |
| 新增/更新 profile | 已实现 | record_event 写入 episode 后 | 合并 weak_points、统计次数、更新概念掌握度 |
| 查询 profile | 已实现 | 需要画像上下文时 | 返回薄弱点、低掌握概念、练习统计 |

## 4. 不同场景的写入差异

| 场景 | event_type | importance | metadata | profile 更新 |
|---|---|---|---|---|
| 学习问答 | qa | 0.5 | doc_ids、mode=learn、agent=tutor、phase=answer | total_qa 递增 |
| 练习答对或普通练习 | practice | 0.4 | score、tags、mode=practice、agent=grader、phase=grade | total_practice 和 avg_score 更新 |
| 练习出错 | mistake | 0.9 | score、tags、mode=practice、agent=grader、phase=grade | 更新 weak_points、concept_mastery、练习统计 |
| 考试评分 | exam | 低分 0.9，否则 0.6 | score、weak_points、mode=exam、agent=grader、phase=grade | 根据 weak_points 更新画像 |

补充说明：学习模式当前主要在流式回答完成后写入 QA；非流式学习回答已经读取 memory，但当前代码中没有对 QA 做同等写入。

## 5. 检索与上下文拼装

| 步骤 | 处理方式 | 关键限制 |
|---|---|---|
| 生成检索条件 | 使用 query、course_name、event_types、mode、agent、phase | 只在同一课程范围内查 |
| 请求级去重 | 相同 memory_search 请求命中缓存 | 避免一次请求中重复查库 |
| 排序 | 重要度优先，其次时间倒序 | mistake 通常更容易靠前 |
| 截断 | 单条记忆正文进入上下文前截断 | 默认控制单条最大字符数 |
| 拼接 | 输出为“相关历史记录”段落 | 由 ContextBudgeter 控制总 memory token 预算 |

## 6. 失败与降级

| 异常情况 | 当前处理 | 对主流程影响 |
|---|---|---|
| 没有检索结果 | memory_search 返回 success=true、results=[] | 不拼 memory，上文继续生成 |
| 检索异常 | 工具返回 success=false 或 runner 捕获异常 | 返回空 memory_context，主回答继续 |
| FTS5 不可用 | 自动切换到 LIKE 检索 | 召回质量下降，但功能可用 |
| 写入失败 | 调用方捕获异常并记录日志 | 不阻断用户本次回答 |
| metadata JSON 异常 | 读取时退回空对象 | 该条扩展信息不可用 |
| profile JSON 异常 | weak_points 或 concept_mastery 退回空结构 | 画像部分信息缺失 |
| episode 已写入但 profile 更新失败 | episode 可能已保留，画像未同步刷新 | 后续检索仍可命中 episode，但画像统计可能滞后 |

## 7. 当前边界

| 能力 | 当前状态 |
|---|---|
| 记忆写入策略 | 按场景规则写入，尚未引入 LLM 判断“是否值得记忆” |
| 敏感信息治理 | 当前未做专门脱敏、审计和保留周期管理 |
| 用户可控删除 | 当前未提供面向用户的删除、清空、导出入口 |
| 记忆冲突处理 | 当前以追加和画像合并为主，未做版本冲突仲裁 |
| 长期遗忘/衰减 | 当前 importance 不自动衰减 |
