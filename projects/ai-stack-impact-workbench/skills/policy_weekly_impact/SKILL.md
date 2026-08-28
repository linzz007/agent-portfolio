# policy_weekly_impact

触发条件：

- 用户点击“分析最近一周政策影响”。
- 用户要求根据企业信息分析近期政策机会、风险、材料缺口和行动建议。

能力边界：

- 读取 Company Wiki 和本地企业记忆。
- 读取最近政策样本和政策条款。
- 输出 P0-P4 影响等级、理由、政策证据和企业证据。
- 生成报告和 run_artifact。
- 不自动修改企业知识库；纠错必须生成 proposal 并经用户确认。

Stage flow：

```text
load_company_context
-> fetch_recent_policies
-> policy_ingest_and_index
-> retrieve_relevant_clauses
-> extract_policy_clauses
-> match_company_policy
-> score_policy_impact
-> review_evidence_and_risk
-> generate_weekly_report
```
