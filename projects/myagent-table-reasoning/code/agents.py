""" 与 MACT（NAACL 2025）相关的工具类与函数。

版权 (c) 2025 Robert Bosch GmbH

本程序是自由软件：你可以按照自由软件基金会发布的 GNU Affero 通用公共许可证第 3 版或（你可以选择的）更高版本的条款重新发布和/或修改。

本程序的发布目的是希望它有用，但不提供任何保证；甚至不包含适销性或特定用途适用性的默示保证。详情参见 GNU Affero 通用公共许可证。

你应该已经收到一份 GNU Affero 通用公共许可证副本；如果没有，请访问 <https://www.gnu.org/licenses/>。
"""

import re
import string
from collections import Counter, OrderedDict, defaultdict

import pandas as pd
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import find_dotenv, load_dotenv
from fewshots_table import (DEMO_CRT, DEMO_CRT_DIRECT, DEMO_SCITAB,
                            DEMO_SCITAB_DIRECT, DEMO_TAT, DEMO_TAT_DIRECT,
                            DEMO_WTQ, DEMO_WTQ_DIRECT,
                            NUMERICAL_OPERATION_EXAMPLE,
                            TABLE_OPERATION_EXAMPLE, DEMO_DATABENCH,
                            NUMERICAL_OPERATION_EXAMPLE_LONG_TABLE, GLOBAL_PLAN_EXAMPLES,
                            NUMERICAL_OPERATION_EXAMPLE_LONG_TABLE_GLOBAL)
from langchain import Wikipedia
from langchain.agents.react.base import DocstoreExplorer
from llm import OpenSourceLLM
from openai import AzureOpenAI
from prompts_table import (DIRECT_AGENT, NUMERICAL_OPERATION_PROMPT,
                           TABLE_OPERATION_PROMPT, react_agent_prompt_crt,
                           react_agent_prompt_scitab, react_agent_prompt_tat,
                           react_agent_prompt_wtq, NUMERICAL_OPERATION_PROMPT_LONG_TABLE,
                           NUMERICAL_OPERATION_PROMPT_LONG_TABLE_GLOBAL,
                           react_agent_prompt_databench, global_plan_prompt)
from sglang import assistant, function, gen, user
from tot import llm_reward, vote_prompt_as
from utils import (extract_from_outputs, parse_action, table2df,
                   table_linear)

all_input_token, all_output_token = 0, 0


def load_gpt_azure():
    """Initialize an Azure OpenAI client using local credentials.

    通过 .env 中的配置和 Azure 身份凭据获取 token provider，随后构造
    AzureOpenAI 客户端，供 GPT 规划/生成阶段复用。
    """
    _ = load_dotenv(find_dotenv())
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(
            exclude_managed_identity_credential=True
        ),
    )
    client = AzureOpenAI(
        api_version="",
        azure_ad_token_provider=token_provider,
        azure_endpoint="")
    return client

# client = load_gpt_azure()


def get_completion(prompt, client, n, model="gpt-35-turbo"):
    """Call Azure GPT to obtain n generations for a given prompt.

    会把请求编码成 ChatCompletion 格式，追踪输入/输出 token 数方便成本估算，
    并将所有候选的 content 列表返回给上层（如规划器或代码生成器）。
    """
    global all_input_token, all_output_token
    messages = [{"role": "user", "content": prompt}]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.6,
        max_tokens=400,
        top_p=0.95,
        frequency_penalty=0,
        presence_penalty=0,
        n=n,
        stop=None
    )
    input_token_num = response.usage.prompt_tokens
    output_token_num = response.usage.completion_tokens
    all_input_token += input_token_num
    all_output_token += output_token_num
    # print(all_input_token, all_output_token)
    return [item.message.content for item in response.choices]


@function
def table_operation(s, instruction, table_df):
    """Invoke the table-op LLM to fetch relevant rows/cols.

    根据 instruction 和给定的 DataFrame 代码片段，构造 Few-shot Prompt，
    交给 sglang 管理的推理服务执行，生成的新表结果会被写入状态 s。
    """
    prompt = TABLE_OPERATION_PROMPT.format(
        instruction=instruction, table_df=table_df, examples=TABLE_OPERATION_EXAMPLE)
    s += user(prompt)
    s += assistant(gen("result", max_tokens=2000, temperature=0.6))


@function
def code_revise(s, current_error, extracted_code, table_df):
    """Ask LLM to patch previously generated code.

    将错误日志、原始代码片段以及表格上下文一起交给 LLM，请其生成修正后的
    Python 代码块。结果依旧由 sglang 的状态机返回。
    """
    prompt = f"You are an expert in revising code. The following code results in an error when executing on the table dataframe (the dataframe only shows the first two records of original data due to its large size). Please revise the code to address the error and only return the revised code in one python code block. \n Table dataframe: {table_df}\n Erroneous code: {extracted_code}\n Error message: {current_error}\n Revised code:"
    s += user(prompt)
    s += assistant(gen("result", max_tokens=2000, temperature=0.6))


@function
def numerical_operation(s, instruction, table_df):
    """Generate Python code that performs numerical reasoning on a table.

    该函数在普通长度的表格上构建 few-shot 提示，要求 LLM 写出完整的
    pandas 代码，并把结果写入 sglang 状态，供后续执行。
    """
    prompt = NUMERICAL_OPERATION_PROMPT.format(
        instruction=instruction, table_df=table_df, examples=NUMERICAL_OPERATION_EXAMPLE)
    s += user(prompt)
    s += assistant(gen("result", max_tokens=4000, temperature=0.6))


@function
def numerical_operation_long_table(s, instruction, table_df, global_planning=False):
    """Long-table variant of numerical_operation used for Databench/global plan.

    由于长表只提供前几行示例，这里会要求 LLM 返回一个 target_function，
    让后端在真实 parquet 数据上执行；global_planning=True 时换用含计划的模板。
    """
    if global_planning:
        prompt = NUMERICAL_OPERATION_PROMPT_LONG_TABLE_GLOBAL.format(
            instruction=instruction, table_df=table_df, examples=NUMERICAL_OPERATION_EXAMPLE_LONG_TABLE_GLOBAL)
    else:
        prompt = NUMERICAL_OPERATION_PROMPT_LONG_TABLE.format(
            instruction=instruction, table_df=table_df, examples=NUMERICAL_OPERATION_EXAMPLE_LONG_TABLE)
    s += user(prompt)
    s += assistant(gen("result", max_tokens=4000, temperature=0.6))


@function
def direct_code(s, prompt):
    """Send an arbitrary prompt to sglang and capture the textual/code response.

    该接口用于 direct reasoning 模式，允许我们直接把自定义指令交给代码模型，
    例如“继续补全代码并将答案存入 result”。
    """
    s += user(prompt)
    s += assistant(gen("result", max_tokens=4000, temperature=0.6))


def validate_gloabl_result(executed_results, threshold=3):
    """Check whether multiple execution results agree strongly enough.

    全局规划会运行多份代码，这里简单地以出现次数最多且超过阈值的结果为胜者，
    这样能过滤掉偶发的错误执行。
    """
    answer = Counter(executed_results).most_common(1)[0][0]
    frequency = Counter(executed_results).most_common(1)[0][1]
    if frequency >= threshold and answer != "":
        return True, answer
    else:
        return False, answer


class ReactAgent:
    """Unified agent that plans, retrieves, calculates, and answers one sample.

    每个样本（表格 + 文本 + 问题）都会绑定一个 ReactAgent，它管理规划模型、
    代码模型、工具调用、奖励选择以及 scratchpad，使得整个 REACT 闭环在一个
    类里实现，方便调度与日志记录。
    """
    def __init__(self,
                 question: str,
                 table: str,
                 table_df: str,
                 df_path: str,
                 context: str,
                 key: str,
                 answer: str = '',
                 plan_model_name: str = '',
                 code_model_name: str = '',
                 model=None,
                 tokenizer=None,
                 max_steps: int = 5,
                 task: str = '',
                 codeagent_endpoint=None,
                 plan_sample: int = 5,
                 code_sample: int = 5,
                 max_actual_steps: int = 5,
                 as_reward='consistency',
                 use_pre_answer=False,
                 answer_aggrement=1.0,
                 direct_reasoning=False,
                 without_tool=False,
                 long_table_op='ignore',
                  code_as_observation=False,
                  debugging=False
                  ) -> None:
        """Cache raw sample data, configure prompts/models, and reset state."""
        vllm = model
        if "gpt" not in plan_model_name:
            self.llm = OpenSourceLLM(
                model_name=plan_model_name,
                model=model,
                vllm=vllm,
                tokenizer=tokenizer
            )
        if "gpt" in plan_model_name or "gpt" in code_model_name:
            # self.client = load_gpt_azure()  too slow
            self.client = client
        else:
            self.client = None
        self.tokenizer = tokenizer
        self.question = question
        self.table_string = table_linear(
            table, num_row=None) if isinstance(table, list) else table
        self.long_table = False
        self.debugging = debugging
        # if len(table) * len(table[0]) > 50:  # 10*5    300
        #     self.long_table = True
        #     if long_table_op == 'short-table':
        #         self.table_string = table_linear(table, num_row=5)   ##num_row=20
        #         remain = len(table) - 5
        #         self.table_string += f"\n[...Remaining {remain} rows not shown due to large table size...]"
        self.table_df = table_df
        self.table_dfs = [table_df]
        self.df_path = df_path
        self.context = context
        self.answer = answer
        self.plan_model_name = plan_model_name
        self.code_model_name = code_model_name
        self.key = " ".join(key) if isinstance(key, list) else key
        self.max_steps = max_steps
        self.codeagent_endpoint = codeagent_endpoint
        self.plan_sample = plan_sample
        self.code_sample = code_sample
        self.max_actual_steps = max_actual_steps
        self.as_reward = as_reward
        self.task = task
        self.evaluator_output = []
        self.use_pre_answer = use_pre_answer
        self.pre_ans_all = []
        self.docstore = DocstoreExplorer(Wikipedia())  # Search
        self.answer_aggrement = answer_aggrement
        self.direct_reasoning = direct_reasoning
        self.without_tool = without_tool
        self.long_table_op = long_table_op
        self.code_as_observation = code_as_observation
        self.llm_sampled = []
        self.code_sampled = []
        self.direct_sampled = []

        if not self.direct_reasoning:
            if task == "tat":
                self.react_examples = DEMO_TAT
                self.agent_prompt = react_agent_prompt_tat
            elif task == "scitab":
                self.react_examples = DEMO_SCITAB
                self.agent_prompt = react_agent_prompt_scitab
            elif task == "crt":
                self.react_examples = DEMO_CRT
                self.agent_prompt = react_agent_prompt_crt
            elif task == "wtq":
                self.react_examples = DEMO_WTQ
                self.agent_prompt = react_agent_prompt_wtq
            elif task == "databench":
                self.react_examples = DEMO_DATABENCH
                self.agent_prompt = react_agent_prompt_databench
                self.global_plan_prompt = global_plan_prompt
                self.global_plan_examples = GLOBAL_PLAN_EXAMPLES

        else:
            self.agent_prompt = DIRECT_AGENT
            if task == "tat":
                self.react_examples = DEMO_TAT_DIRECT
            elif task == "scitab":
                self.react_examples = DEMO_SCITAB_DIRECT
            elif task == "crt":
                self.react_examples = DEMO_CRT_DIRECT
            elif task == "wtq":
                self.react_examples = DEMO_WTQ_DIRECT
            self.code_prompt = self.agent_prompt.split("[BREAK]")[-1].strip()
            self.code_examples = self.react_examples.split(
                "[BREAK]")[-1].strip()
            self.text_prompt = self.agent_prompt.split("[BREAK]")[0].strip()
            self.text_examples = self.react_examples.split("[BREAK]")[
                0].strip()

        self.__reset_agent()

    def code_extract_retrieve(self, code_strings):
        """Execute generated retrieval code and return the resulting table snippet."""
        rows = []
        new_table = ""
        p = re.compile(r"```[Python|python].*```", re.DOTALL)
        try:
            executable_code = re.findall(p, code_strings)[0]
            executable_code = "\n".join(executable_code.split("\n")[1:-1])
            df_string = self.table_df
            executable_code = "\n".join([df_string, executable_code])
            loc = {}
            exec(executable_code, globals(), loc)
            new_table = loc['new_table']
        except:
            pass
        if isinstance(new_table, pd.Series):
            new_table = new_table.to_frame()
        if isinstance(new_table, pd.DataFrame):
            if not new_table.empty:
                # to string format
                header = new_table.columns.tolist()
                rows = new_table.values.tolist()
                rows.insert(0, header)
        return rows

    def retriever_tool(self, instruction):
        """Generate table-filtering code, run it, and surface the textual slices."""
        max_attempt = self.code_sample
        results = []
        results2dfs = defaultdict(list)
        if self.code_model_name == self.plan_model_name:
            # use one base model
            prompt = TABLE_OPERATION_PROMPT.format(
                instruction=instruction, table_df=self.table_df, examples=TABLE_OPERATION_EXAMPLE)
            messages = [{"role": "user", "content": prompt}]
            if "gpt" not in self.code_model_name:
                codes = self.llm(
                    messages, num_return_sequences=max_attempt, return_prob=False)
            else:
                codes = self.prompt_agent_gpt_coder(prompt)

            for code_strings in codes:
                rows = self.code_extract_retrieve(code_strings)
                if rows != []:
                    result = table_linear(rows, num_row=None).strip()
                    results2dfs[result].append(table2df(rows))
                else:
                    result = ""
                results.append(result)

        else:
            # code generation batching
            if "gpt" not in self.code_model_name:
                batch_data = [{"instruction": instruction,
                               "table_df": self.table_df} for i in range(max_attempt)]
                states = table_operation.run_batch(
                    batch_data, progress_bar=True, backend=self.codeagent_endpoint)
                code_strings = [s["result"] for s in states]
            else:
                prompt = TABLE_OPERATION_PROMPT.format(
                    instruction=instruction, table_df=self.table_df, examples=TABLE_OPERATION_EXAMPLE)
                code_strings = self.prompt_agent_gpt_coder(prompt)

            for code_string in code_strings:
                rows = self.code_extract_retrieve(code_string)
                if isinstance(rows, list) and rows != []:
                    # if len(rows) > 7:  # not showing the rest
                    #     remain = len(rows) - 7
                    #     result = table_linear(rows, num_row=7).strip(
                    #     ) + f"\n[...Remaining {remain} rows not shown due to large table size...]"
                    # else:
                    result = table_linear(rows, num_row=None)
                    results2dfs[result.strip()].append(table2df(rows))
                else:
                    result = ""
                results.append(result)

        results = [res for res in results if not res == ""]
        try:
            sorted_df = sorted(results2dfs, key=lambda key: len(
                results2dfs[key]), reverse=True)
            target_df = list(sorted_df.values())[0][0]
            self.table_dfs.append(target_df)
        except:
            pass
        return results

    def calculator_tool(self, eqution, recent_table_df):
        """Evaluate a symbolic instruction locally; fallback to numerical_tool."""
        def clean_eqution(eqution):
            # 输入：eqution(str)原始算式
            # 输出：str，清理后的算式
            # 作用：去除逗号和美元符号，便于计算执行。
            eqution = eqution.replace(",", "")
            eqution = eqution.replace("$", "")
            return eqution
        try:
            eqution = clean_eqution(eqution)
            loc = {}
            eqution_ = "result = "+eqution
            exec(eqution_, globals(), loc)
            if self.without_tool:
                return [], ""
            else:
                return loc['result'], ""
        except:
            result = ""
            # try with the coder
            try:
                result = self.numerical_tool(
                    eqution, recent_table_df, self.df_path, global_planning=False)
            except:
                pass
            return result

    def code_extract_calculator(self, code_strings, table_df, original_df):
        """Run generated numerical code and parse both scalar/table outputs."""
        result = ""
        rows = []
        p = re.compile(r"```[Python|python].*```", re.DOTALL)
        if not self.task == "databench":
            try:
                executable_code = re.findall(p, code_strings)[0]
                executable_code = "\n".join(executable_code.split("\n")[1:-1])
                df_string = table_df
                executable_code = "\n".join([df_string, executable_code])
                loc = {}
                exec(executable_code, globals(), loc)
                result = loc['final_result']
            except:
                # print(e)
                pass
            if isinstance(result, pd.Series):
                result = result.to_frame()

            if isinstance(result, pd.DataFrame) and not result.empty:
                # to string format
                header = result.columns.tolist()
                rows = result.values.tolist()
                rows.insert(0, header)
                result = table_linear(rows, num_row=None)

            if not isinstance(result, str):
                try:
                    # if it is numpy array
                    rows = result.tolist()
                    result = table_linear(rows, num_row=None)
                except:
                    result = str(result)
            return result, rows, None, None
        else:
            current_error = None
            try:
                executable_code = re.findall(p, code_strings)[0]
                executable_code = "\n".join(executable_code.split("\n")[1:-1])
                # make sure only function is returned
                return_ids = [i for i, line in enumerate(executable_code.split(
                    "\n")) if "return" in line and "#" not in line.split("return")[0]]
                if return_ids:
                    return_ids = return_ids[-1]
                    executable_code = "\n".join(
                        executable_code.split("\n")[:return_ids+1])
                executable_code = "\n".join(
                    ["import pandas as pd\nimport numpy as np\nimport pandas\nimport numpy\n", executable_code, f"final_result=target_function(original_df)"])
                loc = {"original_df": original_df}
                exec(executable_code, globals(), loc)
                result = loc['final_result']
            except Exception as e:
                # print(e)
                current_error = e
                executable_code = None
            if isinstance(result, pd.Series):
                result = result.to_frame()
            if isinstance(result, pd.DataFrame) and not result.empty:
                # to string format
                self.original_df = result
                header = result.columns.tolist()
                rows = result.values.tolist()
                rows.insert(0, header)
                if len(result) > 10:
                    # too long
                    remain_line = len(result) - 4
                    result = table_linear(
                        rows, num_row=3) + f"\n ...[remaining {remain_line} rows not shown due to large table size]..."
                    rows = rows[:3]
                else:
                    result = table_linear(rows, num_row=None)

            if not isinstance(result, str):
                # result is a variable
                with open("temp.txt", "w") as f:
                    print(result, file=f)
                with open("temp.txt", "r") as f:
                    result = f.readlines()
                result = "\n".join(result)
            return result, rows, current_error, executable_code

    def numerical_tool(self, instruction, table_df, df_path=None, global_planning=False):
        """Batch-generate/evaluate numerical code and optionally record DF outputs."""
        max_attempt = self.code_sample
        results, generated_code = [], []
        results2df = defaultdict(list)
        if df_path:
            original_df = pd.read_parquet(df_path, engine='pyarrow')

        if self.code_model_name == self.plan_model_name:
            prompt = NUMERICAL_OPERATION_PROMPT.format(
                instruction=instruction, table_df=table_df, examples=NUMERICAL_OPERATION_EXAMPLE)
            messages = [{"role": "user", "content": prompt}]
            if "gpt" not in self.code_model_name:
                codes = self.llm(
                    messages, num_return_sequences=max_attempt, return_prob=False)
            else:
                codes = self.prompt_agent_gpt_coder(prompt)
            for code_strings in codes:
                result, rows = self.code_extract_calculator(
                    code_strings, table_df, original_df)
                if result != "" and rows != []:
                    try:
                        result = result.strip()
                        results2df[result].append(table2df(rows))
                    except:
                        pass
                results.append(result)

        else:
            if "gpt" not in self.code_model_name:
                # code generation batching
                batch_data = [{"instruction": instruction, "table_df": table_df}
                              for i in range(max_attempt)]
                if self.task != "databench":
                    states = numerical_operation.run_batch(
                        batch_data, progress_bar=True, backend=self.codeagent_endpoint)
                else:
                    if not global_planning:
                        states = numerical_operation_long_table.run_batch(
                            batch_data, progress_bar=True, backend=self.codeagent_endpoint)
                    else:
                        batch_data = [{"instruction": instruction, "table_df": self.table_df, "global_planning": True}
                                      for i in range(max_attempt)]
                        states = numerical_operation_long_table.run_batch(
                            batch_data, progress_bar=True, backend=self.codeagent_endpoint)
                code_strings = [s["result"] for s in states]

            else:
                prompt = NUMERICAL_OPERATION_PROMPT.format(
                    instruction=instruction, table_df=table_df, examples=NUMERICAL_OPERATION_EXAMPLE)
                code_strings = self.prompt_agent_gpt_coder(prompt)

            for code_string in code_strings:
                result, rows, error, extracted_code = self.code_extract_calculator(
                    code_string, table_df, original_df)
                if result != "" and rows != []:
                    try:
                        result = result.strip()
                        results2df[result].append(table2df(rows))
                    except:
                        pass
                results.append(result)
                generated_code.append(extracted_code)
        if not global_planning:
            results = [res for res in results if not res == ""]
            try:
                sorted_df = sorted(results2df, key=lambda key: len(
                    results2df[key]), reverse=True)
                target_df = list(sorted_df.values())[0][0]
                self.table_dfs.append(target_df)
            except:
                pass
            if self.code_as_observation:
                if len(results) > 0:
                    results = Counter(results).most_common(1)[0][0]
            return results
        else:
            self.generated_code = generated_code
            return results

    def as_llm(self, thoughts, actions, observations):
        """Score sampled reasoning paths with an external LLM judge."""
        all_paths = ""
        assert len(thoughts) == len(actions)
        if len(thoughts) > 0:
            all_paths = f"Question: {self.question}\nTable:{self.table_string}Past reasonings:{self.scratchpad}\n"
            current_paths = ""
            for i, (t, a, o) in enumerate(zip(thoughts, actions, observations)):
                sc = "\n".join([t, a, o])
                all_paths += f'current reasoning path {i+1}: {sc}\n'
                current_paths += f'current reasoning path {i+1}: {sc}\n'
            outputs, _, _ = llm_reward(reasoning_paths=all_paths, vote_prompt=vote_prompt_as, model_type="open",
                                       model_name=self.plan_model_name, tokenizer=self.tokenizer, model=self.llm)
            self.evaluator_output.append([current_paths, outputs])
            target_choice = extract_from_outputs(outputs, len(thoughts))
            target_thought = thoughts[target_choice]
            target_action = actions[target_choice]
            try:
                target_observation = observations[target_choice]
            except:
                target_observation = ""
        else:
            target_thought, target_action, target_observation = "", "", ""
        return target_thought, target_action, target_observation

    def as_reward_fn(self, sampled):
        """Parse REACT samples and decide which action to execute this step."""
        global all_input_token, all_output_token

        def get_current_step(instance):
            # 输入：instance(str)单条采样轨迹
            # 输出：tuple(thought, action, observation) 当前步文本片段
            # 作用：从轨迹中提取当前步对应的 Thought、Action、Observation。
            current_thought, current_action, current_observation = "", "", ""
            if instance:
                instance_ = [line for line in instance.split(
                    "\n") if line.strip() != ""]
            try:
                current_thought = [
                    line for line in instance_ if f"Thought {self.step_n}:" in line][0]
                current_action = [
                    line for line in instance_ if f"Action {self.step_n}:" in line][0]
                current_observation_start_id = [i for i, line in enumerate(
                    instance_) if f"Observation {self.step_n}:" in line]
                current_observation_end_id = [i for i, line in enumerate(
                    instance_) if f"Thought {self.step_n+1}:" in line]
                current_observation = "\n".join(
                    instance_[current_observation_start_id[0]:current_observation_end_id[0]])
            except:
                pass
            return current_thought, current_action, current_observation

        def get_preliminary_ans(sampled):
            # 输入：sampled(list[str])采样轨迹列表
            # 输出：tuple(pre_ans, mapping) 预选答案及其来源索引
            # 作用：统计 Finish 动作中的答案，按一致性阈值挑出预选答案。
            mapping = []
            threshold = len(sampled)*self.answer_aggrement
            pre_ans = None
            pre_answers = []
            for i, instance in enumerate(sampled):
                try:
                    instance_ = [line for line in instance.split(
                        "\n") if line.strip() != ""]
                    answer_line = [
                        line for line in instance_ if "Finish" in line]
                    if len(answer_line) > 0:
                        _, pre_answer = parse_action(answer_line[0])
                        pre_answers.append(pre_answer.lower())
                        mapping.append(i)
                except:
                    pass
            try:
                most_common, num_most_common = Counter(
                    pre_answers).most_common(1)[0]  # val, times
            except:
                most_common = ""
                num_most_common = 0
            if num_most_common > threshold:
                pre_ans = most_common
            assert len(pre_answers) == len(mapping)
            return pre_ans, pre_answers, mapping

        def as_rollout(sampled, actions):
            # 输入：sampled(list[str])采样轨迹；actions(list[str])对应动作
            # 输出：str，基于 roll-out 预答案选择的目标动作
            # 作用：从采样中找出最常见预答案对应的动作，作为下一步选择。
            _, pre_ans_all, mapping = get_preliminary_ans(sampled)
            try:
                common = Counter(pre_ans_all).most_common(1)[0][0]
                sampled_id = [i for i, item in enumerate(
                    pre_ans_all) if item == common]
                sampled_id = [mapping[item] for item in sampled_id]
            except:
                pass
            try:
                target_action = actions[sampled_id[0]]
            except:
                target_action = ""
            return target_action

        def as_consistency(action_thought, observations):
            # 输入：action_thought(defaultdict)动作到思路列表映射；observations(list[str])观察集合
            # 输出：tuple(thought, action, observation) 基于一致性的代表路径
            # 作用：按动作出现频次选择主导动作，并返回对应思路及最常见观察。
            target_thought, target_action, target_observation = "", "", ""
            if target_thought == "" and target_action == "":
                action_thought = OrderedDict(
                    sorted(action_thought.items(), key=lambda x: len(x[1]), reverse=True))
                # majority action
                try:
                    target_action = list(action_thought.keys())[0]
                    target_thought = [
                        item for item in action_thought[target_action] if item != ""][0]
                    try:
                        target_observation = Counter(
                            observations).most_common(1)[0][0]
                    except:
                        pass
                except:
                    pass
            return target_thought, target_action, target_observation

        thoughts, actions, observations = [], [], []
        pre_ans = None
        action_thought = defaultdict(list)
        action_observation = defaultdict(list)
        # get perliminary answer

        if self.as_reward == "logp" or self.as_reward == "combined":
            log_probs = sampled.pop(-1)

        if self.step_n == 1:
            pre_ans, pre_ans_all, _ = get_preliminary_ans(sampled)
            self.pre_ans = pre_ans
            self.pre_ans_all = pre_ans_all

        target_sample = []
        for i, item in enumerate(sampled):
            t, a, o = get_current_step(item)
            if not t == "" and not a == "":
                thoughts.append(t)
                actions.append(a)
                observations.append(o)
                action_thought[a].append(t)
                action_observation[a].append(o)
                target_sample.append(i)

        if self.as_reward == "consistency":
            target_thought, target_action, target_observation = as_consistency(
                action_thought, observations)

        elif self.as_reward == "llm":
            target_thought, target_action, target_observation = self.as_llm(
                thoughts, actions, observations)

        elif self.as_reward == "logp":
            target_thought, target_action, target_observation = "", "", ""
            log_probs = [log_probs[item] for item in target_sample]
            assert len(log_probs) == len(actions)
            try:
                target_action = actions[log_probs.index(max(log_probs))]
                target_thought = [
                    item for item in action_thought[target_action] if item != ""][0]
                try:
                    target_observation = [
                        item for item in action_observation[target_action] if item != ""][0]
                except:
                    pass
            except:
                pass

        elif self.as_reward == "rollout":
            target_thought, target_action, target_observation = "", "", ""
            target_action = as_rollout(sampled, actions)
            try:
                target_thought = [
                    item for item in action_thought[target_action] if item != ""][0]
                try:
                    target_observation = [
                        item for item in action_observation[target_action] if item != ""][0]
                except:
                    pass
            except:
                pass

        elif self.as_reward == "combined":
            target_thought, target_action, target_observation = "", "", ""
            ac_lst = []
            _, ac, _, = as_consistency(action_thought, observations)
            ac_lst.append(ac)
            _, ac, _ = self.as_llm(thoughts, actions, observations)
            ac_lst.append(ac)
            log_probs = [log_probs[item] for item in target_sample]
            try:
                target_action = actions[log_probs.index(max(log_probs))]
                ac_lst.append(ac)
            except:
                pass
            ac = as_rollout(sampled, actions)
            ac_lst.append(ac)
            target_action = Counter(ac_lst).most_common(1)[0][0]
            try:
                target_thought = [
                    item for item in action_thought[target_action] if item != ""][0]
                try:
                    target_observation = [
                        item for item in action_observation[target_action] if item != ""][0]
                except:
                    pass
            except:
                pass

        return target_thought, target_action, target_observation, observations

    def get_answer_from_llm(self, instance) -> str:
        """Extract the final answer fragment from a text-only generation."""
        return instance.split(":")[-1].strip()

    def get_answer_from_code(self, instance) -> str:
        """Execute a fenced code block and capture the `result` variable."""
        # exec
        p = re.compile(r"```[Python|python].*```", re.DOTALL)
        try:
            executable_code = re.findall(p, instance)[0]
            executable_code = "\n".join(executable_code.split("\n")[1:-1])
            df_string = self.table_df
            executable_code = "\n".join([df_string, executable_code])
            loc = {}
            exec(executable_code, globals(), loc)
            result = loc['result']
        except:
            result = ""
        if not isinstance(result, str):
            result = str(result)
        return result

    def run(self, reset=True, given_plan=None) -> None:
        """Drive the agent until it halts/finishes, optionally with a preset plan."""
        if reset:
            self.__reset_agent()
        if self.task == "databench":
            if not self.is_finished():
                self.global_planning(given_plan)
        while not self.is_halted() and not self.is_finished():
            # if global planning fail, try step-wise planning
            self.step()

        if not self.answer:
            if self.use_pre_answer:
                try:
                    self.answer = Counter(
                        self.pre_ans_all).most_common(1)[0][0]
                except:
                    # direct prompting
                    self.answer = self.get_quick_answer()
            else:
                # direct prompting
                self.answer = self.get_quick_answer()

    def step(self) -> None:
        """Perform one reasoning step: sample → select → execute → log.

        根据 direct_reasoning 与否走两条分支，要么直接生成答案投票，要么进行
        Planner→Reward→Tool 的全流程，并持续更新 scratchpad 方便下一步引用。
        """
        if self.direct_reasoning:  # 判断是否处于直接推理模式
            llm_sampled = self.prompt_agent(mode="text")  # 仅文本模式采样答案
            llm_sampled_ = [self.get_answer_from_llm(
                item) for item in llm_sampled]  # 将文本输出转换为答案
            prompt = self.code_prompt.format(
                examples=self.code_examples, table=self.table_df, question=self.question, context=self.context)  # 准备代码提示词
            code_sampled = [direct_code.run(prompt, backend=self.codeagent_endpoint)[
                "result"] for i in range(self.code_sample)]  # 调用代码代理生成代码答案
            code_sampled_ = [self.get_answer_from_code(
                item) for item in code_sampled]  # 执行代码并提取结果
            self.llm_sampled = [item for item in llm_sampled_ if item != ""]  # 过滤空文本答案
            self.code_sampled = [item for item in code_sampled_ if item != ""]  # 过滤空代码结果
            self.direct_sampled = self.llm_sampled + self.code_sampled  # 合并两种答案
            self.history = [llm_sampled, code_sampled]  # 保存原始样本历史
            self.answer = Counter(self.direct_sampled).most_common(1)[0][0]  # 多数票确定答案
            self.finished = True  # 标记任务完成

        else:  # 常规 REACT 模式
            ### 这一部分是planner的代码
            if "gpt" in self.plan_model_name:  # 判断规划模型是否为 GPT
                sampled = self.prompt_agent_gpt()  # 调用 GPT 获取 Thought/Action
            else:
                sampled = self.prompt_agent(mode="both")  # 调用开源 LLM 获取 Thought/Action
            self.actual_step_n += 1  # 实际步数加一
            ### planners 的输出必须交给选举器（as_reward_fn）
            thought, action, observation, all_observations = self.as_reward_fn(
                sampled)  # 奖励函数选择当前步执行的候选
            if self.use_pre_answer and self.pre_ans:  # 判断是否已有预答案
                self.finished = True  # 直接结束
                self.answer = self.pre_ans  # 使用预答案
            else:
                if thought != "" and action != "":  # 确保当前步有效
                    if "Finish" not in action:  # 处理非 Finish 动作
                        action_type, argument = parse_action(action)  # 解析动作类型与参数
                        if action_type == "Calculate":  # 计算动作
                            recent_table_df = self.table_dfs[-1]  # 取最近一次的表格 DF
                            new_ob = self.calculator_tool(
                                argument, recent_table_df=recent_table_df)  # 调用计算工具
                            if not isinstance(new_ob, list):  # 若返回单值
                                if new_ob != "":
                                    observation = f"Observation {self.step_n}: {new_ob}"  # 直接构造 Observation
                            else:
                                if new_ob != []:
                                    new_ob = [
                                        f'Observation {self.step_n}: {item}' for item in new_ob]  # 将多结果转换为 Observation
                                new_ob += all_observations  # 与 LLM 提示的 Observation 合并
                                observation = Counter(
                                    new_ob).most_common(1)[0][0]  # 多数票选 Observation

                        elif action_type == "Retrieve":  # 检索动作
                            new_ob = self.retriever_tool(
                                instruction=argument)  # 调用检索工具
                            if new_ob != []:
                                new_ob = [
                                    f'Observation {self.step_n}: {item}' for item in new_ob]  # 格式化 Observation
                                if not self.long_table and not self.code_as_observation:
                                    new_ob += all_observations  # 可选与 LLM Observation 合并
                                observation = Counter(
                                    new_ob).most_common(1)[0][0]  # 多数票选择

                        elif action_type == "Search":  # 搜索外部知识
                            if self.without_tool:
                                pass  # 禁止工具时跳过
                            else:
                                try:
                                    observation_wiki = self.docstore.search(
                                        argument)  # 访问 Wikipedia
                                    observation = f"Observation {self.step_n}: {observation_wiki}"  # 生成 Observation
                                except Exception as e:
                                    pass  # 搜索失败则忽略
                        elif action_type == "Operate":  # Operate 视为计算
                            recent_table_df = self.table_dfs[-1]  # 获取当前 DF
                            new_ob = self.calculator_tool(
                                argument, recent_table_df=recent_table_df)  # 调用计算工具
                            if new_ob != "":
                                observation = f"Observation {self.step_n}: {new_ob}"  # 构造 Observation

                        if observation != "":  # 若观测有效
                            self.scratchpad += thought + "\n"  # 记录 Thought
                            self.scratchpad += action + "\n"  # 记录 Action
                            self.scratchpad += observation + "\n"  # 记录 Observation
                            self.step_n += 1  # 推进步号

                    else:
                        action_type, argument = parse_action(action)  # 解析 Finish 动作
                        self.answer = argument  # 直接写入答案
                        self.scratchpad += thought + "\n"  # 记录 Thought
                        self.scratchpad += action + "\n"  # 记录 Action
                        self.finished = True  # 标记完成

                else:
                    pass  # Thought/Action 无效则跳过
                print("==============current step===========")  # 打印分隔符
                print(self.scratchpad)  # 输出当前 scratchpad

    def prompt_agent_gpt(self) -> str:
        """Send the current REACT prompt to Azure GPT and return raw generations."""
        prompt = self._build_agent_prompt()
        preds = get_completion(prompt, client=self.client, n=self.plan_sample)
        return preds

    def prompt_agent_gpt_coder(self, prompt) -> str:
        """Helper for GPT-based coding models to batch-generate code snippets."""
        preds = get_completion(prompt, client=self.client, n=self.code_sample)
        return preds

    def global_planning(self, given_plan) -> None:
        """Databench shortcut: run a high-level plan once and validate results."""
        if not given_plan:
            plan = self.get_global_plan()[0]
            plan = plan.split("Plan:")[-1].strip()
            self.generated_plan = plan
        else:
            self.generated_plan = given_plan
            plan = given_plan
        executed_results = self.numerical_tool(
            plan, self.table_df[0], self.df_path, global_planning=True)
        valid, result = validate_gloabl_result(executed_results)
        if valid:
            self.answer = result
            self.finished = True

    def get_quick_answer(self):
        """Fallback: direct QA prompt without intermediate actions.

        当 REACT 循环失败或超时后，退回到最初的 direct few-shot QA，通过
        多个样本的多数票提供一个兜底答案。
        """
        if self.task == "tat":
            examples = DEMO_TAT_DIRECT
        elif self.task == "scitab":
            examples = DEMO_SCITAB_DIRECT
        elif self.task == "crt":
            examples = DEMO_CRT_DIRECT
        elif self.task == "wtq":
            examples = DEMO_WTQ_DIRECT
        text_prompt = DIRECT_AGENT.split("[BREAK]")[0].strip()
        text_examples = examples.split("[BREAK]")[
            0].strip()
        prompt = text_prompt.format(
            examples=text_examples,
            table=self.table_string,
            context=self.context,
            question=self.question)
        if "gpt" not in self.plan_model_name:
            answer = self.llm(
                prompt, num_return_sequences=self.plan_sample, return_prob=False)
        else:
            answer = get_completion(
                prompt, client=self.client, n=self.plan_sample)
        answers = [ans.split(":")[-1].strip() for ans in answer]
        answer = Counter(answers).most_common(1)[0][0]
        return answer

    def prompt_agent(self, mode="both") -> str:
        """Call the local OpenSourceLLM with either text-only or full REACT prompt.

        mode="text" 时只生成直接答案，mode="both" 则输出 Thought/Action 序列；
        当需要 logprob（logp/combined 奖励）时也会要求模型返回概率。
        """
        prompt = self._build_agent_prompt(mode=mode)
        if self.as_reward == "logp" or self.as_reward == "combined":
            return_prob = True
        else:
            return_prob = False
        return self.llm(prompt, num_return_sequences=self.plan_sample, return_prob=return_prob)

    def get_global_plan(self):
        """Use few-shot examples to ask the LLM for a Databench plan.

        只返回一条 plan 字符串，由 global_planning 再分割执行。
        """
        prompt = self.global_plan_prompt.format(
            examples=self.global_plan_examples,
            table=self.table_string,
            context=self.context,
            question=self.question)
        return self.llm(prompt, num_return_sequences=1, return_prob=False)

    def _build_agent_prompt(self, mode="both") -> str:
        """Assemble the full prompt (few-shot examples + current table/context)."""
        if mode == "text":
            return self.text_prompt.format(
                examples=self.text_examples,
                table=self.table_string,
                context=self.context,
                question=self.question)
        elif mode == "both":
            return self.agent_prompt.format(
                examples=self.react_examples,
                table=self.table_string,
                context=self.context,
                question=self.question,
                scratchpad=self.scratchpad)

    def is_finished(self) -> bool:
        """Return True when the agent has produced an answer."""
        return self.finished

    def is_correct(self) -> bool:
        """Exact-match check between prediction and ground-truth."""
        if not isinstance(self.answer, str):
            self.answer = str(self.answer)
        return EM(self.answer, self.key)

    def is_halted(self) -> bool:
        """Signal whether the agent exceeded step limits without finishing."""
        return ((self.step_n > self.max_steps) or (self.actual_step_n > self.max_actual_steps)) and not self.finished

    def __reset_agent(self) -> None:
        """Clear scratchpad and counters so the same object can solve a new sample."""
        self.step_n = 1
        self.actual_step_n = 1
        self.finished = False
        self.scratchpad: str = ''

    def set_qa(self, question: str, key: str) -> None:
        """Update the question/answer fields in case the agent is reused."""
        self.question = question
        self.key = key


def normalize_answer(s):
    """Normalize text by stripping articles/punctuation and lowercasing."""
    def remove_articles(text):
        # 输入：text(str)
        # 输出：str，移除英语冠词后的文本
        # 作用：去掉 a/an/the 以减少不必要的差异。
        return re.sub(r"\b(a|an|the)\b", " ", text)

    def white_space_fix(text):
        # 输入：text(str)
        # 输出：str，压缩多余空格后的文本
        # 作用：统一空白字符格式。
        return " ".join(text.split())

    def remove_punc(text):
        # 输入：text(str)
        # 输出：str，去除标点后的文本
        # 作用：删除所有标点符号，简化比较。
        exclude = set(string.punctuation)
        return "".join(ch for ch in text if ch not in exclude)

    def lower(text):
        # 输入：text(str)
        # 输出：str，小写形式的文本
        # 作用：统一大小写确保比较一致。
        return text.lower()

    return white_space_fix(remove_articles(remove_punc(lower(s))))


def EM(answer, key) -> bool:
    """Exact-match metric built on top of normalize_answer."""
    return normalize_answer(answer) == normalize_answer(key)
