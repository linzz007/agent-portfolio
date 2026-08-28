# `code/` 目录文件说明

概览 MACT 主代码目录中各文件的职责与核心逻辑，便于理解整体架构与模块间的依赖。

## `code/agents.py`
- 实现核心的 `ReactAgent`，负责表格问答的计划、工具调用与答案聚合。支持逐步推理（ReAct）与直接推理两种模式，可根据任务（WTQ/TAT/CRT/SciTAB/DataBench）加载对应示例与提示词。
- 封装 Azure OpenAI 调用（`load_gpt_azure`、`get_completion`）以及基于 sglang 的函数式接口：`table_operation`（生成操作表格的代码）、`numerical_operation`/`numerical_operation_long_table`（数值运算/长表处理）、`code_revise`（根据报错修复生成代码）、`direct_code`（直接代码生成）。
- 工具链：`retriever_tool` 生成并执行代码提取子表；`calculator_tool` 执行公式或回落到 `numerical_tool`；`numerical_tool` 生成/批量执行代码完成数值运算，并在 DataBench 中支持全局规划；`code_extract_*` 系列负责从代码块中提取/执行代码并将结果转成表格字符串或最终数值。
- 采样与奖励：`as_reward_fn` 提供一致性、LLM 评估、logp、rollout、combined 等多种策略选取本轮的 Thought/Action/Observation；`summarize_react_trial`、`extract_from_outputs` 等辅助选择或统计样本。
- 运行流程：`run` 控制完整迭代（含 DataBench 全局计划）、`step` 执行单步 Thought/Action/Observation 并依据解析出的动作调用对应工具；`get_quick_answer` 作为直接回答回退；`get_answer_from_code/llm` 负责解析输出。`normalize_answer`/`EM` 用于答案匹配。

## `code/utils.py`
- 表格与数据处理工具：`clean_cell` 规范单元格、`check_header` 去重列名、`table_linear` 将列表格式表格转 Markdown、`table2df` 生成可执行的 DataFrame 构造代码、`dfcode2str` 从 DataFrame 构造代码还原预览表。
- 结果解析与随机选择：`parse_action` 解析 ReAct 动作字符串，`extract_from_outputs` 从模型输出抽取或随机选择路径，`summarize_react_trial` 统计代理表现。
- 数据加载：`get_databench_table` 从 parquet 载入 DataBench 表，抽取前若干行作为提示中的缩略表并返回路径。

## `code/prompts_table.py`
- 定义所有提示模板（使用 LangChain `PromptTemplate`）：ReAct 指令（TAT/WTQ/CRT/SciTAB/DataBench）、直接回答与直接代码提示（`DIRECT_AGENT`）、表操作与数值运算提示（包括长表与全局规划版本）、以及 DataBench 全局计划提示。
- 明确允许的动作集合（Retrieve/Calculate/Search/Operate/Finish 等）和输出格式，为 `ReactAgent` 选择任务专属模板。

## `code/fewshots_table.py`
- 收录各任务的 few-shot 示例与代码示例：`DEMO_WTQ/CRT/TAT/SCITAB/DATABENCH` 及对应的直接推理版本，示范 Thought/Action/Observation 流程；`TABLE_OPERATION_EXAMPLE`、`NUMERICAL_OPERATION_EXAMPLE`（含长表与全局规划版本）提供代码生成的参考样例；`GLOBAL_PLAN_EXAMPLES` 用于 DataBench 的全局规划。
- 内容主要为文本/代码块，无执行逻辑，供提示词拼装使用。

## `code/llm.py`
- 开源模型推理封装 `OpenSourceLLM`，支持 vLLM 生成与可选 logprob 返回。针对不同模型家族（Qwen/Phi/Mistral/LLaMA）配置采样参数、填充 token，并提供 `get_log_scores`、`get_sampled_scores` 以基于生成日志计算序列概率。

## `code/tot.py`
- Tree-of-Thought 辅助：定义投票提示 `vote_prompt_as`、`vote_prompt_obs` 与评分提示 `score_prompt`；`llm_reward` 将若干推理路径与投票提示拼接后调用闭源或开源 LLM 进行评分，返回最优路径的文本。

## `code/tqa.py`
- 命令行入口。解析模型/数据集相关参数，加载数据集（WTQ/CRT/TAT/SciTAB/DataBench），初始化规划模型与可选代码模型（vLLM 或 Azure OpenAI），构建 `ReactAgent` 实例并遍历数据集运行，每条结果输出为 JSON 行（含答案、思路轨迹等）。
- 支持 CodeLlama 模板注册、长表处理策略、不同奖励模式、直接推理/无工具等开关，并在调试模式下仅跑单条样本打印预测。

## 其他
- `code/temp.txt`：执行自动生成代码时的临时输出占位文件（用于将非字符串结果转写成文本）。
