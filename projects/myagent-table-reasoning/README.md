# myAgent Table Reasoning

面向复杂表格问答的风险自适应多智能体推理系统。项目目标是在 WTQ / TabFact / CRT 等表格推理任务中，根据问题风险动态选择轻量路径、确定性算子或增强协作路径，在保证可解释性的同时降低 token 成本。

## Highlights

- **Task-aware Contract**：先判断答案类型、证据需求和输出契约，再进入推理路径。
- **Risk-adaptive Routing**：低风险问题走轻量证据包，高风险问题触发更强协作或验证。
- **Evidence Builder**：根据题干、表头、候选行列、缺失值和操作词构造可审计证据包。
- **Deterministic Operators**：为比较、计数、聚合、数值判断等表格任务提供可插拔算子。
- **Evaluation Reports**：记录准确率、执行失败、延迟和 provider-returned token usage。

## Structure

```text
code/                 core reasoning pipeline and evaluation scripts
tests/                unit tests for routing, contracts, risk control and evaluation
datasets_ready/       public/sample benchmark slices
docs/                 experiment notes and sanitized technical reports
```

## Quick Start

```bash
pip install -r requirements.txt
python code/sample_benchmark.py
pytest
```

## Repository Scope

本仓库不公开专利交底书、专利报告 docx 或未公开权利要求文本，只保留可展示的工程代码、公开数据样例和实验报告。
