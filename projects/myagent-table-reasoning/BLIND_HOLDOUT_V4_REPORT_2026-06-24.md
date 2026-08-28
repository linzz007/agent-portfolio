# myAgent V4 盲测与 MACT 对比报告

## 1. 结论

当前版本已经具备稳定运行、三数据集适配、统一评估和低 token 消耗能力，
但“总体准确率优于 MACT”的目标没有在全新盲测上实现。

V4 最终盲测结果：

| 系统 | 正确数 | 准确率 | 平均实际 token | 执行失败 |
|---|---:|---:|---:|---:|
| myAgent | 45/60 | 75.0% | 2,349 | 0/60 |
| MACT | 50/60 | 83.3% | 8,867 | 0/60 |

myAgent 的 token 为 MACT 的 26.5%，节省约 73.5%，但准确率低 8.3 个百分点。
配对精确 McNemar 检验 `p=0.2266`，在 60 题规模下差异不显著；这不等于
myAgent 已追平或优于 MACT。

## 2. 盲测协议

- 模型：`deepseek-v4-flash`
- 温度：`0`
- thinking：关闭
- 每数据集 20 条，总计 60 条
- WTQ：20 个不同表
- TabFact：20 个不同表，真假各 10 条
- CRT：20 个不同表，分层为 7/7/6
- 历史排除：186 个题目 ID、176 个表 ID
- 历史题目重叠：0
- 历史表重叠：0
- 两套系统使用完全相同的冻结输入
- 输入 SHA-256 记录在
  `datasets_ready/blind_holdout_v4_2026-06-24/manifest.json`

V4 冻结后没有修改代码；六组输出全部完成后才统一评分。

## 3. 分数据集结果

| 数据集 | myAgent | MACT | 差值 | myAgent token | MACT token |
|---|---:|---:|---:|---:|---:|
| WTQ | 11/20 (55%) | 16/20 (80%) | -25pp | 1,966 | 9,516 |
| TabFact | 17/20 (85%) | 19/20 (95%) | -10pp | 2,970 | 8,053 |
| CRT | 17/20 (85%) | 15/20 (75%) | +10pp | 2,111 | 9,031 |

myAgent 在 CRT 上领先，在 WTQ 和 TabFact 上落后。V4 的 95% Wilson 区间：

- myAgent 总体：62.8% 到 84.2%
- MACT 总体：72.0% 到 90.7%

配对结果为：两者都对 42 条、仅 myAgent 对 3 条、仅 MACT 对 8 条、两者都错
7 条。

## 4. V3 与开发回放

第一批 V3 盲测按最终统一指标复算：

- myAgent：44/60（73.3%）
- MACT：50/60（83.3%）
- myAgent/MACT token 比：41.5%
- McNemar `p=0.2101`

V3 被揭盲并用于通用错误诊断后，最终代码在该已消耗集合上的开发回放达到
52/60（86.7%），MACT 为 50/60（83.3%），token 比为 24.5%。该结果只能说明
修复覆盖了已知错误类别，不能作为泛化成绩。V4 降至 45/60 证明开发回放结果
没有稳定迁移到下一批未见题。

## 5. 当前代码改造

本轮完成的通用能力：

1. 数据集配置层：WTQ、TabFact、CRT 使用不同答案形状和提示约束，但共享同一
   Router、Compressor、Planner、Calculator 和 Critic 主流程。
2. 动态答案契约：支持 scalar、list、tuple 和 closed-label，包含数量、顺序、
   精度和单位约束。
3. 全表列画像：Planner 可看到预览外的代表值、列类型和缺失标记，文本长度有
   固定上限。
4. 结构化 DataFrame 适配：移除字符串代码生成，支持引号列名、空值、逗号数字、
   尾注 `+` 和重复表头。
5. 执行恢复：真实 Python 错误优先反馈给 Planner；`re` 和 `isinstance` 在受限
   环境内可用，`os` 等系统模块仍被禁止。
6. 自适应 Critic：中低风险题通过执行、grounding 和答案契约后跳过 LLM Critic；
   hard 或多视角模式仍调用 Critic。
7. TabFact 专用核验：对代码结论再次检查实体、数字、日期和复合子句。
8. 统一评估：WTQ denotation、TabFact 二分类、CRT 标准化/有序多值使用同一入口，
   同时报告实际 API token、Wilson 区间和 McNemar 检验。
9. 可复现盲测：支持历史 ID/表 ID 排除、分层抽样、输入哈希和配对比较。

本地最终验证为 113 项测试通过，Python 编译检查通过，`git diff --check` 无错误。

## 6. 验收判断

| 验收项 | V4 结果 | 是否通过 |
|---|---:|---:|
| myAgent 总体准确率不低于 MACT | 75.0% < 83.3% | 否 |
| 至少两个数据集不低于 MACT | 1/3 | 否 |
| token 不超过 MACT 的 40% | 26.5% | 是 |
| 执行失败率不超过 2% | 0% | 是 |

最终判定：**V4 未通过准确率验收，不能宣称总体性能优于 MACT。**

## 7. 论文与专利表述建议

可以由当前证据支持的表述：

- 在相同模型和冻结输入下，myAgent 将平均 token 降低约 73.5%。
- myAgent 在两轮盲测中均保持 0 执行失败。
- V4 CRT 准确率为 85%，高于 MACT 的 75%。
- 数据集配置、动态答案契约、证据压缩、执行恢复和自适应核验形成了完整可运行
  的技术链路。

当前证据不支持的表述：

- myAgent 总体准确率超过 MACT。
- myAgent 在三个数据集上全面优于 MACT。
- 60 题结果具有统计显著性。

更合理的论文主线是“准确率-成本 Pareto 优化与数据集自适应运行时”，而不是
“全面超过 MACT”。若仍要追求总体准确率领先，下一阶段应进行架构升级：对 WTQ
开放实体/时间推理引入受控 ReAct 检索路径，对 TabFact 引入可解释的子句分解与
逐子句证据绑定，而不是继续增加题目级 prompt 规则。

## 8. 产物位置

- V4 冻结输入：`datasets_ready/blind_holdout_v4_2026-06-24/`
- V4 两系统输出：`outputs/blind_holdout_v4_2026-06-24/`
- V4 机器可读汇总：`outputs/blind_holdout_v4_2026-06-24/summary.json`
- V3 最终指标汇总：
  `outputs/blind_holdout_v3_2026-06-24/summary_final_metric.json`
- 最终开发回放：`outputs/generalization_v4_dev/v3_replay_v4b_summary.json`
