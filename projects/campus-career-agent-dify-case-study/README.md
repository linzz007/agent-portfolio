# Campus Career Agent Dify Case Study

基于 Dify 二次开发的高校就业辅导 Agent 脱敏案例仓库。原项目面向学生与教师两类用户，覆盖简历生成、岗位推荐、政策问答、面试辅导和班级就业分析等流程。

> 注意：本仓库不是完整 Dify 平台分发版，而是作品集用途的脱敏案例。真实部署配置、学校接口凭据、数据库卷、用户数据和运行日志均已移除。

## Business Scenario

- 学生侧：简历生成、岗位匹配、政策/课程问答、面试辅导。
- 教师侧：班级就业进展汇总、学生画像管理、报告生成。
- 平台侧：基于 Dify 工作流、知识库、权限角色和操作日志做 ToB 交付适配。

## What This Repository Shows

- `src_examples/models/`：学生、教师、岗位、简历等业务模型的脱敏结构。
- `src_examples/moderation/`：就业辅导场景的内容边界与安全过滤逻辑。
- `src_examples/controllers/`：学生/教师角色识别与业务入口适配示例。
- `docs/operation-log-design.md`：操作日志设计说明。
- `examples/`：外部岗位数据同步和操作日志测试的脱敏示例。

## Architecture

```text
Dify Workflow / Chat App
  -> Intent Routing
  -> Knowledge Retrieval / Policy QA
  -> Resume & Job Matching Tools
  -> Student / Teacher Business Models
  -> Report Artifact / Operation Log
```

## Sanitization

已移除或替换：

- 学校真实接口域名、appId、appSecret、签名参数；
- Docker volume、Redis dump、Weaviate schema/db、数据库备份；
- 真实学生/教师/岗位数据；
- Dify 原始平台大体量源码和 node_modules；
- `.env`、备份环境变量、运行日志。
