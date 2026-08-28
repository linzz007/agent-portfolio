# CoursePilot 21 天工程化补短板方案

适用对象：已经会 `Agent / RAG / Prompt / 多 Agent 编排`，但后端工程化、数据库、缓存、部署、测试还不系统的人。  
目标：不重做新项目，直接把当前 `CoursePilot` 改造成你秋招/大厂 AI 应用岗可以长期复用的主项目。

---

## 1. 先看结论

你这个仓库不缺“AI 味道”，缺的是“工程味道”。

当前仓库已经有的强项：

- `backend/api.py`：已经有 FastAPI、文件上传、SSE 流式输出、workspace 管理。
- `core/orchestration/runner.py`：已经有比较完整的多 Agent 编排逻辑。
- `rag/`：已经有文档解析、切分、向量索引、检索链路。
- `memory/store.py`：已经有 SQLite + FTS 的本地记忆能力。
- `scripts/perf/` + `benchmarks/`：已经有性能追踪和评测资产，这个是你的加分项。

当前仓库最该补的缺口：

1. `backend/api.py` 里还有 `workspaces = {}` 这种内存态注册表，不够工程化。
2. 没有用户体系，没有 `JWT` 登录鉴权。
3. 没有通用 ORM / migration 体系，数据库能力还停留在局部 SQLite。
4. 没有 `Redis` 缓存层，检索、会话、限流都还没工程化。
5. 没有 `Docker / compose` 交付链路。
6. 测试还不够标准化，仓库里已经明确提到要往 `pytest` 迁移。

这意味着你的学习顺序应该是：

`FastAPI 工程化 -> SQLAlchemy/Alembic -> JWT -> Redis -> Docker -> pytest -> 性能回归`

不是继续横向学更多 Agent 框架。

---

## 2. 这 21 天怎么学最值

核心原则只有 4 条：

1. 只围绕这一个项目学，不再并行开新坑。
2. 每学一个知识点，必须在仓库里留下真实改动。
3. 先补“能让面试官放心的能力”，再补“看起来高级的能力”。
4. 不推翻现有架构，优先在现有模块上增量改造。

建议主线技术栈：

- Web：`FastAPI`
- ORM：`SQLAlchemy 2.0`
- 迁移：`Alembic`
- 数据库：先 `SQLite` 跑通，再切 `MySQL`
- 缓存：`Redis`
- 部署：`Docker + docker compose`
- 测试：`pytest + TestClient`

为什么这里优先 `SQLAlchemy 2.0`，不是先上 `SQLModel`：

- 你现在不是缺“更少样板代码”，而是缺“更通用的后端面试语言”。
- 大厂面试里，`SQLAlchemy + migration + session + transaction` 的可迁移性更高。
- 你仓库里已经有大量 `Pydantic` schema，直接补 ORM 层更顺。

---

## 3. 你的仓库应该怎么改

### 3.1 现阶段不要推翻的部分

这些模块本身就是你项目的亮点，不要为了“学工程化”把它们拆烂：

- [`core/orchestration/runner.py`](../core/orchestration/runner.py)
- [`core/agents/`](../core/agents)
- [`rag/`](../rag)
- [`scripts/perf/`](../scripts/perf)
- [`benchmarks/`](../benchmarks)

### 3.2 最值得新增的模块

这 21 天里，优先新增下面这些文件：

- `backend/db.py`
- `backend/models.py`
- `backend/deps.py`
- `backend/auth.py`
- `backend/repositories.py`
- `pytest.ini`
- `Dockerfile`
- `compose.yml`
- `.env.example`

### 3.3 最值得改造的现有模块

- [`backend/api.py`](../backend/api.py)
  - 拆出依赖注入、数据库访问、鉴权依赖、健康检查接口。
- [`backend/schemas.py`](../backend/schemas.py)
  - 保留 Pydantic 层，新增用户、会话、消息、workspace 的 schema。
- [`memory/store.py`](../memory/store.py)
  - 学数据库时重点看这份代码，理解表、索引、FTS、查询接口。
- [`tests/`](../tests)
  - 把“脚本式测试”逐步迁到 `pytest`。
- [`scripts/perf/trace_case.py`](../scripts/perf/trace_case.py)
  - 后续做改造前后延迟对比。

---

## 4. 资料索引

下面只留够用的资料，分成两层：

- `P`：Primary，优先看官方 / GitHub
- `S`：Supplement，中文补充，卡住时再看

### 4.1 FastAPI / 工程化

- `P1` FastAPI 官方教程  
  https://fastapi.tiangolo.com/tutorial/
- `P2` FastAPI 依赖注入  
  https://fastapi.tiangolo.com/tutorial/dependencies/
- `P3` FastAPI JWT 鉴权  
  https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
- `P4` FastAPI 测试  
  https://fastapi.tiangolo.com/tutorial/testing/
- `S1` B 站 FastAPI 入门  
  https://www.bilibili.com/video/BV1kQ4y157Ta/
- `S2` CSDN JWT + RBAC  
  https://blog.csdn.net/jcgeneral/article/details/147630190

### 4.2 数据库 / ORM / 迁移

- `P5` SQLAlchemy 2.0 官方教程  
  https://docs.sqlalchemy.org/20/tutorial/
- `P6` Alembic 官方教程  
  https://alembic.sqlalchemy.org/en/latest/tutorial.html
- `P7` MySQL 事务隔离级别官方文档  
  https://dev.mysql.com/doc/refman/8.4/en/innodb-transaction-isolation-levels.html
- `S3` CSDN MySQL 索引与事务补充  
  https://blog.csdn.net/DarkAndGrey/article/details/123479251

### 4.3 Redis / 异步 / HTTP 客户端

- `P8` redis-py 官方指南  
  https://redis.io/docs/latest/develop/clients/redis-py/
- `P9` Python asyncio 官方文档  
  https://docs.python.org/3/library/asyncio.html
- `P10` HTTPX Async 官方文档  
  https://www.python-httpx.org/async/
- `S4` CSDN FastAPI + Redis 实战  
  https://blog.csdn.net/polanpan/article/details/153267712
- `S5` B 站 Redis 教程  
  https://www.bilibili.com/video/BV1m9GCzEEi3/

### 4.4 Docker / 模板 / 参考项目

- `P11` Docker Get Started  
  https://docs.docker.com/get-started/
- `P12` FastAPI Full Stack Template  
  https://github.com/fastapi/full-stack-fastapi-template
- `P13` FastAPI Beyond CRUD  
  https://github.com/jod35/fastapi-beyond-CRUD
- `P14` Agent API（FastAPI + DB 的 Agent 项目参考）  
  https://github.com/agno-agi/agent-api

### 4.5 测试

- `P15` pytest 官方文档  
  https://docs.pytest.org/en/stable/
- `S6` CSDN FastAPI + pytest 补充  
  https://blog.csdn.net/skywalk8163/article/details/143482940

---

## 5. 21 天执行表

说明：

- 每天默认 6 到 8 小时。
- 每天都要有一个“真实产出”。
- 真实产出优先是代码、测试、文档，不是笔记。

### 第一阶段：把现有项目吃透并补上 API 工程化基础

#### Day 1

- 目标：跑通完整请求链路。
- 重点看：
  - [`README.md`](../README.md)
  - [`backend/api.py`](../backend/api.py)
  - [`frontend/streamlit_app.py`](../frontend/streamlit_app.py)
  - [`core/orchestration/runner.py`](../core/orchestration/runner.py)
- 资料：`P1`
- 产出：
  - 画出一张你自己的链路图：`frontend -> /chat/stream -> runner -> agent -> sse`
  - 写出 1 段 150 字以内项目说明

#### Day 2

- 目标：吃透 FastAPI 路由、Schema、依赖注入。
- 重点看：
  - [`backend/api.py`](../backend/api.py)
  - [`backend/schemas.py`](../backend/schemas.py)
- 资料：`P1` `P2` `S1`
- 产出：
  - 列出当前所有 API 的输入、输出、异常分支
  - 新增一个最小健康检查接口：`/healthz`

#### Day 3

- 目标：吃透 SSE 和流式返回。
- 重点看：
  - [`backend/api.py`](../backend/api.py)
  - [`core/llm/openai_compat.py`](../core/llm/openai_compat.py)
  - [`frontend/streamlit_app.py`](../frontend/streamlit_app.py)
- 资料：`P1`
- 产出：
  - 说明当前流式事件里有哪些类型
  - 给 SSE 响应补一条更明确的状态日志

#### Day 4

- 目标：从现有 SQLite 代码补数据库基础。
- 重点看：
  - [`memory/store.py`](../memory/store.py)
  - [`memory/manager.py`](../memory/manager.py)
- 资料：`P7` `S3`
- 产出：
  - 手写当前 `episodes` / `user_profiles` 两张表的结构说明
  - 写出“索引为什么要建”的一句话解释

#### Day 5

- 目标：把当前项目的工程缺口写成改造清单。
- 重点看：
  - [`docs/BACKLOG_REVIEW2_P2.md`](./BACKLOG_REVIEW2_P2.md)
  - [`docs/PROGRESS_REPORT_2026-03-26_FULLFIX.md`](./PROGRESS_REPORT_2026-03-26_FULLFIX.md)
- 资料：无新增
- 产出：
  - 写一版你自己的改造 backlog
  - 明确第一批要加的 4 个文件：`db.py` `models.py` `deps.py` `auth.py`

### 第二阶段：补数据库、用户体系、持久化

#### Day 6

- 目标：引入统一配置和数据库入口。
- 新增：
  - `backend/db.py`
- 资料：`P5`
- 产出：
  - 创建数据库引擎、Session 工厂、`get_db()` 依赖
  - 先用 SQLite 跑通

#### Day 7

- 目标：为 workspace 建正式数据表。
- 新增：
  - `backend/models.py`
- 改动：
  - [`backend/api.py`](../backend/api.py)
- 资料：`P5`
- 产出：
  - 新增 `Workspace` ORM 模型
  - 把 `workspaces = {}` 的一部分查询替换成数据库查询

#### Day 8

- 目标：把 workspace 创建、列表、详情改成数据库持久化。
- 改动：
  - [`backend/api.py`](../backend/api.py)
  - [`backend/schemas.py`](../backend/schemas.py)
- 资料：`P5` `P2`
- 产出：
  - `create_workspace / list_workspaces / get_workspace` 三个接口走 DB
  - 内存字典只保留过渡用途，或者逐步下线

#### Day 9

- 目标：补“用户-会话-消息”这三张核心表。
- 新增或改动：
  - `backend/models.py`
  - `backend/schemas.py`
- 资料：`P5`
- 产出：
  - `User`、`ChatSession`、`ChatMessageRecord` 三张表
  - 能保存至少一轮对话元信息

#### Day 10

- 目标：补 JWT 登录体系。
- 新增：
  - `backend/auth.py`
  - `backend/deps.py`
- 改动：
  - [`backend/api.py`](../backend/api.py)
- 资料：`P3` `S2`
- 产出：
  - 注册 / 登录 / 当前用户信息接口
  - 至少一个受保护接口

#### Day 11

- 目标：补 migration，而不是手改数据库。
- 新增：
  - `alembic.ini`
  - `migrations/`
- 资料：`P6`
- 产出：
  - 第一个 migration：创建 `users / workspaces / sessions / messages`
  - 数据库不再靠“删库重建”演进

### 第三阶段：补 Redis、异步、可靠性

#### Day 12

- 目标：明确 Redis 在这个项目里的位置。
- 重点设计：
  - 检索缓存
  - 会话状态缓存
  - 限流计数
- 资料：`P8` `S4` `S5`
- 产出：
  - 写出 3 个 Redis key 设计
  - 确定第一版只做“检索缓存”

#### Day 13

- 目标：接入 Redis 客户端。
- 新增：
  - `backend/cache.py` 或放入 `backend/deps.py`
- 资料：`P8`
- 产出：
  - 建立 Redis 连接
  - 增加 `ping` 或 cache health check

#### Day 14

- 目标：把 Redis 用到真实链路。
- 改动：
  - [`core/orchestration/runner.py`](../core/orchestration/runner.py)
  - 或 [`rag/retrieve.py`](../rag/retrieve.py)
- 资料：`P8` `P9`
- 产出：
  - 为重复查询增加缓存
  - 至少记录一次命中 / 未命中日志

#### Day 15

- 目标：补异步基础和外部调用规范。
- 改动：
  - 涉及 LLM / 工具 / 网络请求的模块
- 资料：`P9` `P10`
- 产出：
  - 解释清楚当前仓库里哪些地方应该 async，哪些不该乱 async
  - 把 1 个外部请求链路改成规范的 async 写法

#### Day 16

- 目标：补异常处理、日志、健康检查。
- 改动：
  - [`backend/api.py`](../backend/api.py)
  - 可能新增 `backend/errors.py`
- 资料：`P1` `P2`
- 产出：
  - 全局异常处理
  - `/healthz` 返回 app/db/redis 状态
  - 基本请求日志

### 第四阶段：补部署、测试、性能回归

#### Day 17

- 目标：把项目装进容器。
- 新增：
  - `Dockerfile`
  - `.env.example`
- 资料：`P11` `P12` `P13`
- 产出：
  - 单容器能启动后端
  - README 补启动方式

#### Day 18

- 目标：补 compose，多服务联调。
- 新增：
  - `compose.yml`
- 资料：`P11` `P12` `P14`
- 产出：
  - 至少跑起 `app + redis`
  - 如果时间够，再补 `mysql`

#### Day 19

- 目标：把测试体系从“能跑”变成“规范”。
- 新增：
  - `pytest.ini`
- 改动：
  - [`tests/`](../tests)
- 资料：`P4` `P15` `S6`
- 产出：
  - 用 `pytest tests -q` 跑通
  - 至少补 5 个 API 层测试

#### Day 20

- 目标：做一次改造前后性能对比。
- 重点看：
  - [`scripts/perf/trace_case.py`](../scripts/perf/trace_case.py)
  - [`scripts/perf/bench_runner.py`](../scripts/perf/bench_runner.py)
  - [`benchmarks/`](../benchmarks)
- 资料：无新增
- 产出：
  - 跑一组 smoke benchmark
  - 记录 3 个指标：`e2e latency`、`retrieval latency`、`cache hit`

#### Day 21

- 目标：把项目包装成简历主项目。
- 回看：
  - [`README.md`](../README.md)
  - [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md)
  - 本文档
- 产出：
  - 3 条简历项目描述
  - 1 分钟项目介绍
  - 5 个高频面试问题答案

---

## 6. 这 21 天做完后，你的项目至少要长成这样

最低完成标准：

- 有 `JWT` 登录
- 有正式数据库表，而不是只靠内存字典
- 有 `migration`
- 有 `Redis` 缓存
- 有 `healthz`
- 有 `Dockerfile + compose`
- 有 `pytest`
- 有一组性能回归结果

如果你只做到这里，已经足够把“只会 Agent”升级成：

`会 Agent 的 Python 后端工程候选人`

这比再多学两个 Agent 框架有用得多。

---

## 7. 简历里怎么讲这个项目

不要写成：

- 搭建多 Agent 学习助手
- 实现 RAG 问答
- 调用大模型生成答案

要写成：

- 基于 `FastAPI + 多 Agent 编排 + RAG` 实现课程学习系统，支持学习、练习、考试三类模式，具备流式输出与引用溯源能力。
- 将原有内存态 workspace 管理改造为 `SQLAlchemy + Alembic` 持久化方案，补充用户、会话、消息模型与 `JWT` 鉴权链路。
- 引入 `Redis` 作为检索缓存与会话辅助层，并通过 `pytest` 与性能脚本完成接口验证和回归评测。

---

## 8. 先别做什么

这 21 天里，下面这些先不要碰：

- 不要上 `K8s`
- 不要上微服务拆分
- 不要重写前端
- 不要换新的 Agent 框架
- 不要急着搞复杂任务队列
- 不要为了“高级”把现有可运行链路推翻

如果还有余力，优先加的不是新花样，而是：

1. `MySQL` 替换 SQLite
2. `CI` 跑 pytest
3. 检索缓存命中统计
4. 异步索引构建任务

---

## 9. 你接下来最正确的执行方式

如果你现在就开始做，建议顺序是：

1. 今天只做 Day 1 到 Day 2，把链路吃透。
2. 明天开始直接动数据库层，不要再继续纯看文档。
3. 每完成一个阶段，就顺手更新 README 和简历描述。

这份计划的本质不是“学后端”，而是把 `CoursePilot` 从一个强 AI demo，抬到一个能讲工程落地的项目。
