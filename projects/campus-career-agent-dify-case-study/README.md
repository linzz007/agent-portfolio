# Campus Career Agent Dify Case Study | 高校就业辅导 Agent 脱敏案例

这是一个基于 Dify 二次开发的 **高校就业辅导 Agent ToB 交付案例**。原项目面向学生和教师两类用户，覆盖简历生成、岗位推荐、政策问答、面试辅导和班级就业分析。本仓库只保留可公开的脱敏代码片段和设计说明，用来展示业务建模与平台适配能力。

> 本仓库不是完整 Dify 平台分发版。真实部署配置、学校接口凭据、数据库卷、用户数据和运行日志均已移除。

## 面试官先看

- **业务问题**：学生侧需要个性化求职辅导，教师侧需要班级就业进展汇总；同一套系统要同时处理不同角色、不同数据权限和不同业务流程。
- **技术重点**：在 Dify 工作流/知识库基础上做业务模型、角色权限、内容安全、操作日志和外部岗位数据同步适配。
- **可追问点**：为什么选择 Dify 二次开发、学生/教师角色如何区分、岗位数据如何同步、政策问答如何接知识库、操作日志为什么重要。

## 核心设计

1. **双角色业务建模**：学生侧关注简历、岗位、面试辅导；教师侧关注班级统计、学生画像和报告生成。
2. **Dify 工作流适配**：将简历生成、岗位推荐、政策问答等流程拆成可编排节点，并通过知识库和工具能力补齐业务链路。
3. **权限与入口控制**：根据学生/教师角色进入不同业务入口，避免教师端统计能力和学生端个人辅导能力混在一起。
4. **外部岗位数据同步**：把外部岗位接口接入本地业务模型，支持岗位推荐和教师端统计分析。
5. **内容安全与操作日志**：通过 moderation 和 operation log 记录关键操作，满足 ToB 项目里的可追踪和安全边界要求。

## 面试官可看的代码入口

| 文件 | 看点 |
| --- | --- |
| `src_examples/models/student_teacher.py` | 学生、教师、班级等核心业务模型。 |
| `src_examples/models/positions.py` | 岗位数据模型和同步后的结构。 |
| `src_examples/models/resume.py` | 简历相关业务结构。 |
| `src_examples/controllers/user_role.py` | 学生/教师角色识别和入口适配。 |
| `src_examples/moderation/cloud_service_llm.py` | 内容安全过滤与 LLM moderation 示例。 |
| `docs/operation-log-design.md` | 操作日志设计说明。 |
| `examples/test_job_api_connect_redacted.py` | 外部岗位接口连接的脱敏示例。 |
| `examples/test_operation_log.py` | 操作日志测试示例。 |

## 项目定位

这个项目适合在面试中表达 **真实 ToB Agent 落地经验**。它不主打底层 Agent Runtime，而是证明我理解业务用户、角色权限、数据接入、知识库问答和平台化交付这些真实项目里绕不开的问题。

## 脱敏说明

已移除或替换：

- 学校真实接口域名、appId、appSecret、签名参数；
- Docker volume、Redis dump、Weaviate schema/db、数据库备份；
- 真实学生/教师/岗位数据；
- Dify 原始平台大体量源码和 node_modules；
- `.env`、备份环境变量、运行日志。
