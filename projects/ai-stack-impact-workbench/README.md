# Insight Workbench | 智能研判 Agent 系统

Insight Workbench 面向个人与团队的信息获取和研判场景。系统以 AI 技术更新、政策规则和行业事件为应用对象，结合 Wiki 画像、受控报告 workflow、Subagent 核验与运行记录，把外部资料转化为可追问、可核查、可复盘的分析报告。

## 待解决问题

- 外部资料分散在新闻、官方文档、社区文章和政策文本中，人工筛选成本高。
- 通用对话模型可以总结资料，但容易脱离用户自己的项目背景和关注目标。
- 报告生成涉及采集、清洗、去重、分析和生成多个阶段，单次模型调用难以稳定约束。
- 分析结论如果缺少来源、角色质疑和运行记录，后续很难追问或复盘。

## 关键动作

1. **受控报告 Workflow**
   将 `/report` Skill 嵌入主 `AgentLoop`，在政策分析链路中以 `State` 保存中间结果、`Graph` 控制阶段流转，通过 Context 投影、ToolGateway 与 Hook / Gate 限定可见信息、工具权限和阶段输出，支持有限重试、失败终止与 HTML 报告生成。

2. **Subagent 调度与上下文隔离**
   针对单次分析缺少专门质疑、子任务上下文和工具权限容易混用的问题，在主 `AgentLoop` 中按需委派分析、质疑和核验任务；通过角色配置、上下文隔离、工具白名单、执行预算和 Schema 校验约束子任务，将结果回填主 Agent，并用 Trace 关联父子任务。

3. **Wiki 画像与背景复用**
   通过 Wiki 维护背景事实、关注方向和项目上下文，以 SQLite 同步事实索引，结合本轮问题检索相关背景；按 token 预算组织 Wiki、历史消息和报告摘要，保留事实与资料引用，支持画像更新后的背景复用和报告追问。

4. **证据与产物留存**
   报告产物、工具调用、上下文片段、子任务结果和异常信息进入运行记录，方便区分“工具调用成功”和“业务结论正确”。

## 项目边界

这个项目不是为了证明“模型比通用 Agent 更聪明”，而是验证一种更接近企业落地的 Agent 组织方式：

```text
固定流程用 workflow 保证执行顺序
不确定分析用 Agent / Subagent 处理
用户背景用 Wiki 画像长期维护
运行过程用 Trace / Artifact 保留证据
```

## 核心模块与代码入口

| 文件 | 作用 |
| --- | --- |
| `run_policy_api.py` | 后端启动入口和对话式 Workbench 服务入口。 |
| `src/policy_impact/app/chat_workbench_service.py` | 对话、报告、trace、artifact 的服务层组织方式。 |
| `src/policy_impact/harness/agent_loop.py` | 主 AgentLoop、动作执行和工具调用闭环。 |
| `src/policy_impact/harness/agent_runtime.py` | 普通对话、report workflow 和子任务委派的统一运行入口。 |
| `src/policy_impact/harness/context_manifest.py` | 上下文包构造与可见上下文记录。 |
| `src/policy_impact/harness/permissions.py` | 工具权限、deny / ask / allow 和策略判断。 |
| `src/policy_impact/harness/subagent_planner.py` | 子智能体委派判断与任务拆分。 |
| `src/policy_impact/harness/artifacts.py` | RunArtifact / 报告产物沉淀。 |
| `src/policy_impact/company_wiki/loader.py` | Wiki 背景事实读取与结构化。 |
| `plugins/skills/manifest.json` | SkillManifest 配置化能力边界。 |

## 验证方式

```powershell
py -3 -m pip install -e .
py -3 -m pytest
py -3 scripts/run_workbench_smoke.py
py -3 scripts/run_workbench_dialogue_acceptance.py
```

## 脱敏说明

发布版已移除真实企业画像、长期记忆数据库、运行日志、本地模型凭据和历史运行数据，只保留可公开样例、测试 fixture、核心代码和设计文档。
