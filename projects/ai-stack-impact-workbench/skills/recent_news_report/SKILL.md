# recent_news_report

## 触发场景

当用户要求“生成最近新闻报告”“分析舆情/新闻对企业的影响”“看看行业动态是否影响公司”时触发。

## 输入

- `company_id`：企业标识，默认 `company_001`
- 新闻源：默认读取 `data/news/raw/*.jsonl`，每行包含 `title/source/published_at/url/summary`
- 企业上下文：读取 Company Wiki 生成的 context pack

## 运行流程

1. 读取本地新闻源，去重、标准化字段。
2. 将新闻结构化为事件，区分 `opportunity`、`compliance_risk`、`capital_signal`、`market_signal`。
3. 用企业 Wiki facts 做 RAG 匹配，找到新闻与企业的连接点。
4. 计算影响分数和高/中/低等级。
5. 输出 Markdown、HTML 和 run artifact。
6. 将报告路径写入本地 SQLite memory。

## 输出

- `report_path`：Markdown 报告
- `html_report_path`：可打开的 HTML 报告
- `artifact_path`：包含 raw_items、cleaned_items、structured_events、analysis 的审计产物

## 工程约束

- 不在这个 skill 内直接修改 Company Wiki。
- 不从社交媒体结论直接推断企业事实，只能作为外部事件证据。
- 高影响判断必须能引用企业事实；无法匹配企业事实时只能给低优先级观察结论。
