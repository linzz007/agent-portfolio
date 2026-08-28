""" 与 MACT（NAACL 2025）相关的工具类与函数。

版权 (c) 2025 Robert Bosch GmbH

本程序是自由软件：你可以按照自由软件基金会发布的 GNU Affero 通用公共许可证第 3 版或（你可以选择的）更高版本的条款重新发布和/或修改。

本程序的发布目的是希望它有用，但不提供任何保证；甚至不包含适销性或特定用途适用性的默示保证。详情参见 GNU Affero 通用公共许可证。

你应该已经收到一份 GNU Affero 通用公共许可证副本；如果没有，请访问 <https://www.gnu.org/licenses/>。
"""

from transformers import AutoModelForCausalLM, AutoTokenizer
# from mistral_common.protocol.instruct.request import ChatCompletionRequest
# from mistral_common.protocol.instruct.messages import UserMessage
# from mistral_common.tokens.tokenizers.mistral import MistralTokenizer
# from mistral_inference.generate import generate
# from mistral_inference.transformer import Transformer
import random
from typing import Union, Literal
from vllm import SamplingParams
import torch
import transformers
import math

transformers.set_seed(42)
random.seed(42)


# def mistral_inference(model, tokenizer, prompts):
#     results = []
#     for prompt in prompts:
#         completion_request = ChatCompletionRequest(
#             messages=[UserMessage(content=prompt["content"])])

#         tokens = tokenizer.encode_chat_completion(completion_request).tokens

#         out_tokens, _ = generate([tokens], model, max_tokens=2000, temperature=0,
#                                  eos_id=tokenizer.instruct_tokenizer.tokenizer.eos_id)
#         result = tokenizer.decode(out_tokens[0])
#         results.append(result)

#     return results


class OpenSourceLLM:
    def __init__(self, model_name, model, vllm, tokenizer):
        # 输入：model_name(str)模型名称，model(模型实例)，vllm(vLLM 引擎实例)，tokenizer(分词器)
        # 输出：无
        # 作用：初始化开源 LLM 包装器，设置模型、分词器以及必要的填充符号。
        self.model_name = model_name
        self.tokenizer = tokenizer
        self.model = model
        self.vllm = vllm
        self.pad_ids = tokenizer.eos_token_id if "mistral" not in model_name.lower() else None
        if self.pad_ids:
            self.tokenizer.pad_token_id = self.pad_ids

    def get_log_scores(self, outputs):
        # 输入：outputs(vLLM 生成的返回对象)
        # 输出：list，每个生成序列的平均对数概率对应的概率值
        # 作用：基于 vLLM 返回的 logprobs 计算生成序列的置信度分数。
        # based on vllm returned log prob
        # log p is only calculated on the desired sequences but not all generated!!!
        ids = self.tokenizer.encode("Observation ")[0]
        sequence_probs = []
        for item in outputs[0].outputs:
            try:
                ending_id = item.token_ids.index(ids)
                target_probs = [list(probs.values())[
                    0].logprob for probs in item.logprobs[:ending_id]]
            except:
                # hit the finish token
                target_probs = [list(probs.values())[
                    0].logprob for probs in item.logprobs]
            try:
                sequence_prob = math.exp(sum(target_probs)/len(target_probs))
            except:
                sequence_prob = 0
            sequence_probs.append(sequence_prob)
        return sequence_probs

    def get_sampled_scores(self, generate_output_sample, input_length):
        # 输入：generate_output_sample(torch 生成返回对象)，input_length(int)输入序列长度
        # 输出：list，每个生成序列的平均对数概率对应的概率值
        # 作用：在非 vLLM 路径下，根据 logits 计算采样序列的概率得分。
        # no vllm needed
        # https://colab.research.google.com/drive/1vLmUfqYdKVo1z2Ztv2V2sQ29nDCYNbFK?usp=sharing#scrollTo=GKpqjMnnnJhK
        sequence_prob = []
        num_sequences = len(generate_output_sample.sequences)

        for seq_index in range(num_sequences):
            probs_sample = []
            target_sequence = generate_output_sample.sequences[seq_index, input_length:]
            pad_end = torch.where(target_sequence != torch.tensor(self.pad_ids))[
                0][-1].tolist() + 1
            target_sequence = target_sequence[:pad_end]

            for i, ids in enumerate(target_sequence):
                logits = generate_output_sample.scores[i][seq_index, :].reshape(
                    (1, -1))
                logprobs = torch.nn.functional.log_softmax(logits, dim=1)
                probs_sample.append(logprobs[0][ids].tolist())
            sequence_prob.append(math.exp(sum(probs_sample)/len(probs_sample)))
        return sequence_prob

    def __call__(self, prompt: str, num_return_sequences: int, return_prob: bool):
        # 输入：prompt(str)用户提示，num_return_sequences(int)返回序列数量，return_prob(bool)是否返回概率
        # 输出：list，包含生成文本序列；当 return_prob 为 True 时额外包含概率列表
        # 作用：根据模型类型调用 vLLM 生成文本，并可选返回对应的概率分数。
        # if "mistral" in self.model_name.lower():
        #     # specifically for mistral nemo model
        #     messages = [{"role": "user", "content": prompt}]
        #     decoded = mistral_inference(self.model, self.tokenizer, messages)

        if "qwen" in self.model_name.lower() or "phi" in self.model_name.lower() or "mistral" in self.model_name.lower():
            decoded = []
            if return_prob:
                sampling_params = SamplingParams(
                    max_tokens=2000, temperature=0.6, top_p=0.95, n=num_return_sequences, logprobs=1)
            else:
                sampling_params = SamplingParams(
                    max_tokens=2000, temperature=0.6, top_p=0.95, n=num_return_sequences)
            outputs = self.vllm.generate([prompt], sampling_params)
            decoded = [item.text for item in outputs[0].outputs]
            if return_prob:
                scores = self.get_log_scores(outputs)
                # return scores as a list
                decoded.append(scores)
        elif "llama" in self.model_name.lower():
            msg = self.tokenizer.apply_chat_template(
                prompt, tokenize=False)
            sampling_params = SamplingParams(
                max_tokens=2000, temperature=0.6, top_p=0.95, n=num_return_sequences)
            outputs = self.vllm.generate([msg], sampling_params)
            decoded = [item.text for item in outputs[0].outputs]

        return decoded

    def encode(self, prompt: str):
        # 输入：prompt(str)用户提示
        # 输出：int，编码后序列的长度
        # 作用：将用户提示通过聊天模版编码成 token 序列并返回长度。
        # if "mistral" in self.model_name.lower():
        #     messages = [{"role": "user", "content": prompt}]
        #     encodeds = self.tokenizer.apply_chat_template(
        #         messages, return_tensors="pt")  # (1,len(input_ids))

        messages = [{"role": "user", "content": prompt}]
        text = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )
        encodeds = self.tokenizer([text], return_tensors="pt").input_ids

        return len(encodeds[0])
