# Agent Workbench：默认对话 + 单报告命令 + Wiki 数据页设计

> 日期：2026-08-05  
> 目标：把 Workbench 收敛成“对话优先”的 Agent Harness。普通输入默认聊天；只有 `/report` 触发重型外部变化影响分析；Wiki 作为独立数据页浏览和维护，不作为聊天模式暴露。

## 1. 要解决的问题

用户真正需要的不是选择“新闻 skill / 政策 skill / 调研 skill”，而是：

- 平时像普通助手一样对话，并能读取长期 Memory 与企业 Wiki。
- 需要严肃分析时，明确生成一份可复盘的外部变化影响报告。
- 能看到系统依据哪些企业资料、事实、数据源和运行证据做判断。
- 报告生成过程可以追踪、审计、回放和回归测试。

因此，公开产品不再暴露 `/news`、`/policy`、`/research`、`/wiki` 等一组内部能力名。用户心智只有三个：

| 用户行为 | 系统行为 |
|---|---|
| 不写斜杠，直接提问 | 默认 `general_chat`，读取 Memory 与 Company Wiki，不主动跑重型 workflow |
| 输入 `/report <要求>` | 单轮触发 `external_impact_report`，内部再判断 source_inventory / policy / news / research |
| 点击 Wiki 页面 | 读取和编辑企业 Wiki 层级数据、结构化 facts 与来源，不触发模型和 Agent Loop |

## 2. 为什么只保留 `/report`

重型报告流程会调用更多工具、写入 Artifact、经过 Gate，并留下更长 Trace。它应该是显式边界，而不是由关键词自动触发。

这样设计解决三个问题：

- **避免误触发**：用户随口问“最新新闻怎么看”，不应该突然跑完整报告链路。
- **边界清晰**：`/report` 意味着用户允许本轮进入外部数据、工具、门控和产物生成。
- **面试可讲**：这是 Harness 的权限边界，不是 prompt 里写一句“请谨慎使用工具”。

后端仍保留旧 `/impact`、`/policy`、`/news`、`/research`、`/wiki` 兼容解析，用于旧测试或旧会话；前端只展示 `/report`。

## 3. 报告内部路线

`external_impact_report` 是唯一公开报告 Skill，内部包含四条路线：

| 内部路线 | 触发条件 | 解决的问题 | 是否调用模型 |
|---|---|---|---|
| `source_inventory` | `/report` 后询问数据源、快照、抓取状态、接入来源 | 展示当前可用数据源、快照数量、来源等级和使用边界 | 否 |
| `policy` | `/report` 后出现政策、监管、法规、合规等问题 | 判断政策条款对企业/项目的适用性和风险 | 是 |
| `news` | `/report` 后出现新闻、最新、发布、开源更新、行业事件 | 从新闻/事件中结构化提取影响点 | 视执行器而定 |
| `research` | `/report` 后要求调研、研究、综述、报告，且没有强政策/新闻实时诉求 | 生成结构化调研报告 | 是 |

旧执行器 `policy_weekly_impact`、`recent_news_report`、`research_report` 保留为内部能力，不再作为主要用户入口。

## 4. 主链路

```text
User message
-> ChatWorkbenchService
-> TurnCoordinator 创建 AgentRun
-> Runtime 解析 slash command
   -> 无 slash：general_chat
   -> /report：external_impact_report
-> ContextManifest 构建本轮可见上下文
-> MemoryStore 按问题读取长期记忆
-> ToolGateway / PermissionEngine 执行允许工具
-> GateRunner 校验关键边界
-> ArtifactStore 写入报告或 source-inventory
-> AgentStep 写入 Trace
-> assistant answer 返回给前端
```

这和固定工作流不冲突，而是分层：

- 对话层负责用户交互。
- Skill 层负责能力边界。
- 内部路线负责复用已有确定性 workflow。
- Harness 层负责上下文、工具、记忆、门控、Trace、Artifact 和回归。

## 5. Wiki 数据页

Wiki 不再是 slash mode。它是 Workbench 的数据面，负责让用户看到并维护系统“知道什么”：

```text
GET /companies/{company_id}/workbench/wiki
-> load_company_knowledge(company_id)
-> index_company_knowledge(profile, pages)
-> 返回 profile / pages / facts / stats
-> 前端按 path parts 渲染层级目录

POST /companies/{company_id}/workbench/wiki/pages
-> 校验 path 必须位于 data/companies/{company_id}/wiki/
-> 写回 markdown 正文
-> 重新解析 FACT 区块
-> 同步 wiki_pages / company_facts SQLite 索引
```

这样做的原因：

- Wiki 是长期企业资料，不是一次性任务。
- 用户应该能浏览当前系统“知道什么”，而不是每次通过聊天让模型总结。
- 用户可以直接修正 Wiki 源资料，修正后会同步到 SQLite，后续聊天和报告使用新 facts。
- 聊天和报告都可以读取 Wiki，但 Wiki 页面本身不需要调用模型。

## 6. 数据持久化

当前持久化边界：

- 会话、消息、AgentRun、AgentStep、Memory、模型配置：SQLite。
- 报告、HTML、RunArtifact、source-inventory：本地 Artifact 文件。
- Company Wiki 源资料：`data/companies/{company_id}/wiki/**/*.md`。
- Company Wiki 检索索引：SQLite `wiki_pages`、`company_facts`。
- 外部数据源配置与快照：`config/sources.json`、`data/news/snapshots/`、`data/policies/snapshots/`。

## 7. 当前实现边界

已经实现：

- 默认消息进入 `general_chat`，不再按关键词自动触发报告。
- 前端公开命令只展示 `/report`。
- `/report` 单轮触发 `external_impact_report`，后续无斜杠消息回到默认聊天。
- 报告内部路线 `source_inventory`、`policy`、`news`、`research`。
- Workbench Wiki API、前端 Wiki 层级页面、markdown 写回和 SQLite facts 重建。
- Trace、Context、Memory、Tool、Gate、Artifact 摘要继续展示在回答上方。

仍需谨慎表述：

- 完整多租户权限、审批暂停恢复、四层版本化 Memory 和完整 Replay 尚未完成。
- 外部实时抓取能力取决于本地数据源配置和快照，不应把 fixture 表述为生产实时数据。
- “显著优于通用 Agent”需要等预算 Eval Artifact 支撑。

## 8. 面试讲法

推荐讲法：

> 我实现了一个面向外部变化影响分析场景的对话型 Agent Workbench。默认消息只做普通聊天，读取 Memory 和企业 Wiki；当用户输入 `/report` 时，系统才进入受控报告 Skill。报告 Skill 内部根据任务选择数据源盘点、政策、新闻或调研路线，并通过 ContextManifest、ToolGateway、Gate、Trace 和 Artifact 记录每一步证据。Wiki 则作为独立数据页展示企业知识库，不再伪装成一个聊天模式。

不要这样讲：

> 我做了新闻、政策、调研和 Wiki 四个 Slash Commands。

前者是在讲 Agent Harness 的边界和证据；后者听起来像几个脚本入口。
