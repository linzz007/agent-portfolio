# Agent Workbench 真实来源、性能与前端验收

日期：2026-07-16

## 1. 验收结论

- 本轮目标已完成：真实新闻源、官方政策源、示例公司 Company Wiki、固定真实模型、Agent Trace、前端桌面/移动端和完整对话矩阵均已验证。
- 固定模型仅保留 `deepseek-v4-flash`。无鉴权或模型名不匹配时，前后端都拒绝执行，不存在本地假模型兜底。
- 最终真实模型矩阵 `9/9` 通过；单元测试 `104/104` 通过；静态 Workbench 验收和动态多轮对话验收均通过。
- 当前前端可作为秋招项目演示和日常单用户使用界面，综合评分 `8.3/10`。它已经达到成熟工程 Demo 水平，但不宣称等同于大厂正式产品的完整设计系统。

## 2. 真实数据源

### 新闻与技术变化

- Claude Code Releases Atom
- OpenAI Codex Releases Atom
- LangGraph Releases Atom
- Model Context Protocol Specification Releases Atom
- Hugging Face Blog RSS
- arXiv Agent Papers API

采集结果：6/6 来源成功，共 62 条事件。强制冷抓取耗时 `6.54s`；相同快照后续读取耗时 `19.56ms`、`2.48ms`。

### 政策与监管

- 国家互联网信息办公室政策法规列表
- 国家网信办人工智能与智能体政策正文页
- 工业和信息化部人工智能科技伦理政策正文页

系统会排除“专家解读、图解、答记者问”等二次解读文章。7 天窗口无新增政策时返回可审计的“无更新”；120 天窗口实测获取 4 份官方文件并完成影响研判。

### 来源控制

- 域名 allowlist 会拒绝后缀伪造域名。
- 每条记录保留 `published_at`、`retrieved_at`、canonical URL、source level 和 SHA-256。
- 新闻快照写入 `data/news/snapshots/`，政策快照写入 `data/policies/snapshots/`。
- TEST fixture 只允许确定性测试或显式离线模式使用，不能作为现实结论。

## 3. 示例公司 Company Wiki

- 企业身份、业务板块、收入与客户、AI 研发和 iFinD 方向改用巨潮资讯官方披露作为主要证据。
- 删除或降级二手媒体对关键事实的支撑。
- 将“已采用 RAG、Prompt、知识库架构”等缺少官方证据的说法改为待核实，不再作为企业事实。
- 风险项明确标记为系统分析推断，并降低置信度，避免和企业披露混淆。

## 4. 本轮发现并修复的问题

1. 新闻来源串行抓取过慢：改为并发抓取和 5 分钟快照缓存。
2. 政策日期范围未真实生效：仓库层现在按 `date_from/date_to` 过滤。
3. 无新增政策被当作失败：改为合法 `no_updates` 终态并生成审计报告。
4. 政策解读文章可能被当成政策正文：增加标题排除规则。
5. 中文单字匹配导致影响分数饱和：改为中文二元/三元 token、Dice 相似度和覆盖率约束。
6. 4 份政策全部被打成 P0/P1/100：重校准权重、上限、证据缺失惩罚和 deadline 条件，结果分布恢复为 P2/P3、48-71 分。
7. 新闻列表被单一 Release 来源占满：增加单来源最多 3 条、总计最多 18 条的多样性控制。
8. 调研模型超长 JSON 两次截断：截断重试不再回灌长残片，改为原始请求加严格压缩约束。
9. 默认 `max_tokens=4096` 导致异常输出停止过晚：收紧为 `2048`。
10. 移动端正文重复展示完整 Windows 路径：正文隐藏重复路径，保留文件名产物胶囊。
11. 移动端模型状态换行、产物名占多行：增加单行省略和稳定宽度约束。

## 5. 性能结果

| 指标 | 优化前完整矩阵 | 优化后完整矩阵 | 变化 |
|---|---:|---:|---:|
| 通过率 | 9/9 | 9/9 | 稳定通过 |
| E2E 平均耗时 | 6877.25 ms | 4121.22 ms | -40.1% |
| E2E 最大耗时 | 29187.95 ms | 8192.80 ms | -71.9% |
| 模型平均耗时 | 7957.85 ms | 3487.42 ms | -56.2% |
| 模型最大耗时 | 28126.32 ms | 6738.22 ms | -76.0% |

专业 Harness 调研场景单独重复 3 次全部通过，E2E 平均 `6270.72ms`、最大 `6780.64ms`。优化前同场景平均 `8566.41ms`，下降约 `26.8%`。

## 6. 前端动态验收

### 已通过

- 1440x900 桌面端无横向溢出、无整页空白滚动，Composer 固定在底部。
- 390x844 移动端无横向溢出，侧边栏可打开/关闭，遮罩有效。
- 聊天记录真实加载并支持搜索；本次测试库显示 328 个会话，侧栏渲染最近会话。
- Slash 面板可选择 `/auto`、`/chat`、`/policy`、`/news`、`/research`、`/wiki`。
- 模型状态位于输入框内，只显示 `deepseek-v4-flash`。
- Trace 位于回答上方，可展开 7 个步骤及输入、输出、工具、门控和原始 JSON。
- 新闻 Trace 能看到 6 个来源的状态、条目数、耗时和校验和；页面未发现 API key 泄露。
- 长路径和长文件名不再破坏移动端布局。

### 设计参考与评分

- 会话侧栏、底部 Composer、模型状态：采用 ChatGPT/LibreChat 一类对话产品的成熟布局习惯。
- Slash 命令面板：采用命令面板式技能发现，而不是把所有 Skill 做成大卡片。
- Trace：采用 Langfuse 的 Session -> Trace -> Observation 思路，但在聊天内只展示与本轮有关的 AgentStep，减少 HTTP/DB 噪声。
- 代码是本项目的原生 HTML/CSS/JS 实现，没有复制 LibreChat、Open WebUI 或 Langfuse 源码。

评分：信息架构 8.7，桌面端视觉 8.5，移动端 8.1，Trace 可读性 8.3，产品完整度 7.8，综合 `8.3/10`。

仍未实现的产品级能力：SSE 流式输出、产物在线预览/下载、会话归档删除、完整键盘导航、暗色主题和独立 Trace 检索页。这些不阻塞本轮 Agent Harness 学习与面试演示目标。

## 7. 验收证据

- 最终真实模型矩阵：`data/acceptance/workbench_api_dialogue_matrix_20260716_152120.json`
- 专业调研三次重复测试：`data/acceptance/workbench_api_dialogue_matrix_20260716_152003.json`
- 新闻四次重复测试：`data/acceptance/workbench_api_dialogue_matrix_20260716_150112.json`
- 静态 Workbench 验收：`data/acceptance/latest-workbench-acceptance.json`
- 动态多轮验收：`data/acceptance/latest-workbench-dialogue-acceptance.json`
- 单元测试：`104 passed`

## 8. 最终验收标准

- [x] 只暴露真实可用模型；无模型明确拒绝执行。
- [x] 新闻与政策使用真实一手来源并保存来源证据。
- [x] 官方来源无新增内容时不使用 fixture 伪造结果。
- [x] Tool Policy、Gate、ContextManifest、Memory、Artifact 和 Trace 可在运行记录中核对。
- [x] 单次对话、多轮对话、Slash 和自动路由均通过。
- [x] 桌面端与移动端无重叠、横向溢出和长路径破版。
- [x] API key 不写入仓库、SQLite、前端或 Trace。
- [x] 最终完整矩阵和单元测试全部通过。
