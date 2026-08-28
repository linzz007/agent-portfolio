# myAgent Table Reasoning | myAgent 表格推理与低成本评测

myAgent Table Reasoning 面向复杂表格问答任务，在 WTQ / TabFact / CRT 等表格推理场景中，根据问题难度和风险动态选择轻量路径、确定性算子或增强验证路径，在保证可解释性的同时降低 token 成本。

## 核心问题

- 表格问答的主要失败点包括找错行列、误解比较条件、数值计算不稳定和证据不可解释。
- 复杂问题如果全部交给强模型完整推理，token 成本和延迟都会显著增加。
- 表格推理需要同时关注准确率、证据链、执行失败率、延迟和 token 使用量。

## 核心设计

1. **Task Contract**：先判断答案类型、证据需求、比较/计数/聚合操作和输出格式，再进入推理链路。
2. **Risk-adaptive Routing**：低风险问题走轻量证据包，高风险问题触发更强协作或验证，避免所有问题都走高 token 路径。
3. **Evidence Builder**：根据题干、表头、候选行列、缺失值和操作词构造可审计证据包。
4. **Deterministic Operators**：为比较、计数、聚合、数值判断等场景提供可插拔算子，减少纯 LLM 计算误差。
5. **Evaluation Reports**：记录准确率、执行失败、延迟和 provider-returned token usage，避免只凭少量样例主观判断。
6. **Ablation / Baseline**：保留 baseline、消融和对比脚本，用于分析每个模块对准确率和成本的影响。

## 核心模块与代码入口

| 文件 | 作用 |
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

## 脱敏说明

发布版不包含专利交底书、专利报告 docx、未公开权利要求文本和大体量实验输出，只保留可展示的工程代码、公开数据样例和实验报告。
