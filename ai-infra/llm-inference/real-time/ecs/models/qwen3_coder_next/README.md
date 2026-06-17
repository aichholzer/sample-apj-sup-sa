# Qwen3-Coder-Next real-time

Per-model real-time/ecs config for
[`Qwen/Qwen3-Coder-Next`](https://huggingface.co/Qwen/Qwen3-Coder-Next),
an 80B-total / 3B-active MoE Apache-2.0 coding model with the qwen3_next
hybrid architecture (Gated DeltaNet + Gated Attention).

## Service shape

| Field | Value |
|---|---|
| `hf_model_id` | `Qwen/Qwen3-Coder-Next` |
| `served_model_name` | `qwen3-coder-next` |
| `tensor_parallel` | 4 |
| `data_parallel` | 1 |
| `gpu_count` | 4 |
| `max_model_len` | 32768 |
| `gated` | no (Apache-2.0) |
| Smallest fit | `g6e.12xlarge` (4xL40S, FP8) |
| Health check | 300s × 10 = 50 min post-StartPeriod tolerance |

## Required serve flags

* `--enable-auto-tool-choice`
* `--tool-call-parser qwen3_coder`
* `--quantization fp8`

## vLLM compatibility

Requires vLLM **>= 0.15.0** for the qwen3_next architecture. The repo
pin (v0.20.2) is well past that floor.

## Sampling

The model does NOT emit `<think>`; do not pass `enable_thinking=True`.
Recommended `temperature=1.0`, `top_p=0.95`, `top_k=40` (sample-side).

## Task

Travel-as-coding: the travel sample data is reused as INPUT, but the
SYSTEM_PROMPT asks for **code generation** (a Python parser dataclass)
rather than the JSON itself.
