# myAgent Table Reasoning | 表格问答推理与低成本评测

这是一个面向复杂表格问答的 **风险自适应多智能体推理系统**。项目在 WTQ / TabFact / CRT 等表格推理任务中，根据问题难度和风险动态选择轻量路径、确定性算子或增强验证路径，在保证可解释性的同时降低 token 成本。

## 面试官先看

- **业务问题**：表格问答常见失败点不是“模型不会说话”，而是找错行列、误解比较条件、数值计算不稳定、复杂问题 token 消耗过高。
- **技术重点**：先做任务契约和风险判断，再决定是否启用压缩表、确定性算子、多视角验证或强模型路径。
- **可追问点**：风险路由怎么判断、证据包怎么构造、确定性算子解决什么问题、怎么做 benchmark、accuracy 和 token 成本如何同时看。

## 核心设计

1. **Task Contract**：先判断答案类型、证据需求、比较/计数/聚合操作和输出格式，再进入推理链路。
2. **Risk-adaptive Routing**：低风险问题走轻量证据包，高风险问题触发更强协作或验证，避免所有问题都走高 token 路径。
3. **Evidence Builder**：根据题干、表头、候选行列、缺失值和操作词构造可审计证据包。
4. **Deterministic Operators**：为比较、计数、聚合、数值判断等场景提供可插拔算子，减少纯 LLM 计算误差。
5. **Evaluation Reports**：记录准确率、执行失败、延迟和 provider-returned token usage，避免只凭少量样例主观判断。
6. **Ablation / Baseline**：保留 baseline、消融和对比脚本，方便解释每个模块是否真的带来收益。

## 面试官可看的代码入口

| 文件 | 看点 |
| --- | --- |
| `code/my_agents.py` | myAgent 主推理链路。 |
| `code/risk_control.py` | 风险评分和路由策略。 |
| `code/evidence_builder.py` | 表格证据包构造。 |
| `code/answer_contracts.py` | 答案契约和输出约束。 |
| `code/dataset_adapters.py` | WTQ / TabFact / CRT 数据适配。 |
| `code/evaluate_results.py` | 结果评测与指标计算。 |
| `code/compare_blind_results.py` | 对比实验结果分析。 |
| `code/calibrate_risk_policy.py` | 风险策略校准。 |
| `tests/test_risk_control.py` | 风险控制单测。 |
| `tests/test_evidence_builder.py` | 证据构建单测。 |
| `BENCHMARK18_REPORT_2026-06-23.md` | 小样本 benchmark 报告。 |
| `BLIND_HOLDOUT_V4_REPORT_2026-06-24.md` | blind holdout 对比报告。 |

## 验证方式

```powershell
py -3 -m pip install -r requirements.txt
py -3 code/sample_benchmark.py
py -3 -m pytest
```

## 项目定位

这个项目适合在面试中表达 **推理任务工程化和评测意识**。它和普通 Agent 应用不同，重点不是对话体验，而是如何把复杂推理任务拆成契约、证据、路由、算子和评测闭环。

## 脱敏说明

发布版不包含专利交底书、专利报告 docx、未公开权利要求文本和大体量实验输出，只保留可展示的工程代码、公开数据样例和实验报告。
