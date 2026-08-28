# Experiment Readiness Report

Date: 2026-06-23

## Scope

- Baseline: `D:\AAAcode\code-code\agent+\MACT-main\MACT-main`
- Patent implementation: `D:\AAAcode\code-code\agent+\myAgent-main`
- Raw datasets: `D:\AAAcode\code-code\agent+\dataset`
- Datasets: WikiTableQuestions (WTQ), Table-Fact-Checking/TabFact, CRT-QA

## Current Verdict

The data layer is now connected for all three datasets through
`code/dataset_adapters.py`. WTQ and TabFact are converted from their native
TSV/CSV layouts. CRT-QA automatically reuses the 359 matching table CSV files
from the sibling TabFact `data/all_csv` directory.

The current Windows machine cannot prove a full local-vLLM run. It uses Python
3.13 and a GTX 1650 4 GB GPU, while the pinned local MACT stack targets Python
3.10, Linux/CUDA, vLLM 0.4.2, and sglang 0.1.16. This limitation no longer
blocks API experiments: both projects now run through the official DeepSeek
OpenAI-compatible endpoint without transformers, vLLM, sglang, Azure,
langchain, or CUDA.

Real `deepseek-v4-flash` requests have been verified locally for both projects.
All three datasets completed a shared, deterministic 18-sample comparison in
MACT and myAgent with provider-returned token usage. The remaining pre-full-run
requirement is a 50-100 sample comparison for both projects on the server;
local evidence is sufficient to proceed to that stage, not to claim a final
benchmark result. See `BENCHMARK18_REPORT_2026-06-23.md` for the current result
and error analysis.

TabFact records now restore original entities hidden as `[UNK]` in the processed
TSV. CRT-QA is mixed-format rather than pure Yes/No: explicit Yes/No and simple
comparison-label records use the closed-label path, while numeric, text, and
calculation-dependent change-direction records use Planner/Calculator.

## Shared 18-Sample Benchmark

All rows use the same 18 records per dataset, seed `20260623`, temperature 0,
thinking disabled, and provider-returned API usage.

| Project | Dataset | Accuracy | Avg API tokens | Avg seconds | Failed exec |
|---|---|---:|---:|---:|---:|
| MACT | WTQ | 83.3% | 11,681 | 6.24 | 0 |
| myAgent | WTQ | 77.8% | 1,497 | 5.52 | 0 |
| MACT | TabFact | 100.0% | 9,442 | 6.88 | 0 |
| myAgent | TabFact | 83.3% | 843 | 2.43 | 0 |
| MACT | CRT-QA | 72.2% | 8,574 | 9.35 | 0 |
| myAgent | CRT-QA | 66.7% | 1,341 | 4.71 | 0 |

Across all 54 records, myAgent used 66,265 API tokens versus MACT's 534,552,
while answering 41 versus 46 records correctly. The sample is an engineering
gate, not a statistically sufficient final result.

## Real DeepSeek Evidence

All rows below use `deepseek-v4-flash`, temperature 0, one candidate, and the
official `https://api.deepseek.com` endpoint.

| Project / mode | Dataset | Samples | EM | Failed exec | Avg calls | Avg tokens | Avg seconds | Avg compression |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| MACT, thinking disabled | WTQ | 5 | 0.40 | 0 | 4.00 | 8773.6 API | 8.09 | 1.000 |
| MACT, thinking disabled | TabFact | 5 | 0.80 | 0 | 2.80 | 6068.4 API | 5.99 | 1.000 |
| MACT, thinking disabled | CRT-QA | 5 | 0.60 | 0 | 3.60 | 7663.8 API | 9.21 | 1.000 |
| myAgent, thinking disabled | WTQ | 5 | 0.60 | 0 | 4.60 | 2800.8 estimated | 7.25 | 0.341 |
| myAgent, thinking disabled | TabFact | 5 | 1.00 | 0 | 3.00 | 907.0 estimated | 2.21 | 0.171 |
| myAgent, thinking disabled | CRT-QA | 5 | 0.40 | 0 | 3.80 | 2047.0 estimated | 5.25 | 0.569 |
| myAgent, thinking enabled | CRT-QA | 5 | 0.60 | 0 | 3.80 | 2157.2 estimated | 27.25 | 0.569 |
| myAgent, thinking disabled | WTQ | 20 | 0.60 | 0 | 4.25 | 2535.1 estimated | 5.20 | 0.253 |

The 20-sample WTQ run routed 8 samples to SIMPLE and 12 to COMPLEX. All 20
records had non-empty answers and compression metrics. Exact results are in
`outputs/real_smoke/myagent_wtq_20_final.jsonl`.

A one-sample `myAgent Full` run with multi-view validation changed the ordinary
path's wrong WTQ value to the gold answer. Evidence Critic passed, Logic Critic
requested replan, alternative execution succeeded, and cross-path validation
passed. This used 13 LLM calls, about 15,946 estimated tokens, and 46.65 seconds,
so Full must remain a separate accuracy/cost ablation rather than the default.

## Dataset Evidence

### WTQ

- `training`: 14,149 samples, 1,679 referenced tables, 0 missing.
- `pristine-seen-tables`: 3,516 samples, 1,450 referenced tables, 0 missing.
- `pristine-unseen-tables`: 4,344 samples, 421 referenced tables, 0 missing.
- Across the three splits: 2,108 unique tables, 0 parse failures.
- 89 raw tables have duplicate column names. The adapter now renames later
  duplicates deterministically, for example `Yacht` and `Yacht_2`.

### TabFact

- `train`: 90,233 samples.
- `dev`: 12,792 samples.
- `test`: 12,779 samples.
- `simple_test`: 4,171 samples.
- `complex_test`: 8,608 samples.
- Across these splits: 16,564 unique tables, 0 missing, 0 malformed TSV rows,
  0 table parse failures.
- Labels are converted to `true` and `false`. Run this data with the existing
  MACT `scitab` fact-checking prompt family.

### CRT-QA

- 728 questions over 359 table ids.
- The CRT-QA directory itself contains no CSV files.
- All 359 ids exist in the sibling TabFact `data/all_csv` directory.
- The adapter auto-detects that directory and can also accept an explicit
  `--table_dir`.

## Prepare Server Data

Run from `myAgent-main`:

```powershell
python code\dataset_adapters.py --dataset wtq `
  --root "D:\AAAcode\code-code\agent+\dataset\WikiTableQuestions-master\WikiTableQuestions-master" `
  --split pristine-unseen-tables `
  --output datasets_ready\wtq_unseen.jsonl

python code\dataset_adapters.py --dataset tabfact `
  --root "D:\AAAcode\code-code\agent+\dataset\Table-Fact-Checking-master\Table-Fact-Checking-master" `
  --split test `
  --output datasets_ready\tabfact_test.jsonl

python code\dataset_adapters.py --dataset crt `
  --root "D:\AAAcode\code-code\agent+\dataset\CRT-QA" `
  --output datasets_ready\crt.jsonl
```

Each output record contains `question`, `statement`, `table_text`, and
`answer`, which are accepted by both generic `tqa.py` entrypoints.

## Recommended Server Environment

Use Linux, CUDA, and Python 3.10. Keep MACT and myAgent in separate Conda
environments initially because the old vLLM/sglang pins are sensitive to CUDA
and PyTorch versions.

```bash
conda create -n mact python=3.10 -y
conda activate mact
pip install -r requirements.txt
python -c "import sglang, transformers, vllm, pandas; print('imports ok')"
```

For comparable baseline/patent results, use the same model, sampling setup,
dataset JSONL, and execution policy for both projects. Both projects now accept
`deepseek`, `openai_compatible`, `azure`, and `local` provider parameters.
MACT preserves its multi-candidate sampling by issuing independent API
requests because DeepSeek Chat Completion does not expose an `n` parameter.
Both MACT and myAgent record provider-returned per-sample request and token
deltas as `api_metrics`; myAgent retains estimated values in `llm_metrics` for
backward compatibility.

## Run myAgent With DeepSeek API

Set the key for the current PowerShell process without putting the key in the
script or command history:

```powershell
$secureKey = Read-Host "DeepSeek API Key" -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
```

Run a small prepared JSONL sample from `myAgent-main`:

```powershell
python code\tqa.py `
  --model_provider deepseek `
  --plan_model_name deepseek-v4-flash `
  --api_base https://api.deepseek.com `
  --api_key_env DEEPSEEK_API_KEY `
  --thinking disabled `
  --temperature 0 `
    --max_tokens 2048 `
  --api_max_retries 5 `
  --dataset_path datasets_ready\wtq_unseen_sample1.jsonl `
  --output_path outputs\myagent_wtq_smoke1.jsonl `
  --task wtq
```

Use `--thinking enabled` for a reasoning-enabled condition. Keep it disabled
for the first cost/latency baseline. To use another OpenAI-compatible service,
set `--model_provider openai_compatible`, its `--api_base`, the desired
`--plan_model_name`, and an environment-variable name through `--api_key_env`.
The legacy Azure and local-vLLM routes remain selectable with
`--model_provider azure` and `--model_provider local`.

Run the matching MACT one-record smoke test from
`D:\AAAcode\code-code\agent+\MACT-main\MACT-main`:

```powershell
python code\tqa.py `
  --model_provider deepseek `
  --plan_model_name deepseek-v4-flash `
  --code_model_name deepseek-v4-flash `
  --api_base https://api.deepseek.com `
  --api_key_env DEEPSEEK_API_KEY `
  --thinking disabled `
  --temperature 0 `
    --max_tokens 2048 `
  --api_max_retries 5 `
  --plan_sample 1 `
  --code_sample 1 `
  --max_step 2 `
  --max_actual_step 2 `
  --dataset_path "D:\AAAcode\code-code\agent+\myAgent-main\datasets_ready\wtq_unseen_sample1.jsonl" `
  --output_path "D:\AAAcode\code-code\agent+\myAgent-main\outputs\mact_wtq_smoke1.jsonl" `
  --task wtq
```

For TabFact use `tabfact_test_sample1.jsonl` with `--task scitab`. For CRT use
`crt_sample1.jsonl` with `--task crt`. The one-record and five-record gates have
now passed locally for both projects. API mode supports MACT reward modes
`consistency`, `rollout`, and `llm`; `logp` and `combined` remain local-only.

The smoke condition intentionally uses temperature 0 and one candidate to
control spend. For a full MACT self-consistency experiment, restore the chosen
paper-style candidate counts (for example 5/5) and a non-zero temperature such
as 0.6. Use the same temperature and thinking setting in myAgent for the fair
comparison; the additional MACT calls are part of the method and must be
reported rather than hidden.

Evaluate both result schemas with the same command from `myAgent-main`:

```powershell
python code\evaluate_results.py outputs\myagent_wtq_smoke1.jsonl
python code\evaluate_results.py outputs\mact_wtq_smoke1.jsonl
```

The evaluator auto-detects `myagent` versus `mact`. It reports dataset-aware
accuracy, LLM calls, token, elapsed-time, and compression in one schema. Use
`avg_total_tokens` when `token_measurement=api_usage`; both API paths now expose
provider-returned usage. Estimated fields remain available as diagnostics.

## Smoke Commands

Run 1-5 samples first by creating JSONL files with `--limit 5`.

MACT, from `MACT-main/MACT-main/code`:

```bash
python tqa.py \
  --plan_model_name <model-name> \
  --code_model_name <same-model-name> \
  --model_path <model-path> \
  --dataset_path <wtq-sample.jsonl> \
  --task wtq \
  --debugging
```

Use `--task crt` for CRT-QA. Use `--task scitab` for TabFact because MACT has
no native `tabfact` prompt option and SciTab is its true/false fact-checking
prompt family.

myAgent, from `myAgent-main`:

```bash
python code/tqa.py \
  --plan_model_name <model-name> \
  --model_path <model-path> \
  --dataset_path <wtq-sample.jsonl> \
  --task wtq
```

Use `--task crt` for CRT-QA and `--task scitab` for TabFact. Add
`--enable_multiview_validation` only for the `myAgent Full` condition because
it adds several LLM calls per complex sample.

Summarize myAgent output with:

```bash
python code/evaluate_results.py <output.jsonl> --error_output <errors.jsonl>
```

## Patent-to-Code Audit

### Implemented in executable code

- LLM-based semantic complexity score (`sem_score`).
- Required-cell coverage score (`cell_score`).
- Weighted total score and easy/medium/hard thresholds.
- Difficulty-aware compression strategies:
  `strict_cell_block`, `expanded_context_block`,
  `evidence_preserving_block`, and `full_table_for_high_coverage`.
- Compression ratio and estimated token compression ratio.
- SIMPLE/COMPLEX routing.
- Deterministic SIMPLE single-cell lookup with evidence.
- COMPLEX Planner -> Calculator -> Critic loop.
- Optional Evidence Critic, Logic Critic, alternative execution, cross-path
  comparison, and evidence summary scaffold.
- LLM call counts, estimated tokens, elapsed time, route/difficulty data,
  execution errors, EM summary, and anomaly export.

The semantic-score parser was hardened during this audit. It now prefers JSON
or an explicit `score` field instead of taking the first number in a verbose
model response.

### Not fully implemented from `point1.txt` and `point2.txt`

- No explicit `coarse_intent` classifier or intent-aware routing/template
  selection.
- Planner output is free-form steps plus Python code, not the documented
  structured PlanStep schema.
- Calculator does not expose the documented structured `step_results` and
  `execution_trace`.
- The two critics run independently; there is no multi-round DebateAgents
  exchange.
- No separate `CollaborationController` that triggers enhanced validation only
  for high-risk cases.
- No calibrated `confidence`, exact `used_cells`, `corrected`, or `high_risk`
  output contract.
- EvidenceAggregator records verdicts and evidence metadata but does not itself
  select or correct a final answer.

Therefore the final patent report's core cost-aware routing/compression method
is implemented, while the broader point-1/point-2 specification is only
partially implemented. The report's wording "optional multi-view validation
scaffold" is accurate; a claim of complete point-2 implementation is not.

## Real-Model Go/No-Go Gates

Before launching full experiments, require all of the following on the server:

1. `python code/tqa.py --help` (or MACT `python tqa.py --help`) exits 0 in the
   project environment.
2. A 5-sample run completes for WTQ, TabFact, and CRT-QA for both projects.
3. No sample has a missing table, empty question, empty answer, or DataFrame
   construction failure.
4. At least 20 mixed SIMPLE/COMPLEX myAgent samples show non-empty answers,
   valid compression metrics, and no systematic execution errors.
5. Run 50-100 samples before the full set and compare MACT vs myAgent EM,
   execution-failure rate, average LLM calls, token estimate, and compression
   ratio.

Gates 1-4 are now satisfied locally with real DeepSeek calls. Gate 5 remains:
run 50-100 samples for both projects on the server before launching full splits.
The current evidence supports server staging, but the 5/20-sample EM values are
not statistically sufficient for a patent or paper performance claim.
