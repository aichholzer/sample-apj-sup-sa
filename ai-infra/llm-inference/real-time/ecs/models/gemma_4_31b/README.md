# Gemma-4-31B real-time

Per-model real-time/ecs config for
[`google/gemma-4-31B-it`](https://huggingface.co/google/gemma-4-31B-it),
a 31B Apache-2.0 dense model with 256K native context (released Apr 2026).
Gemma 4 needs vLLM >= 0.11; the smoke driver pins `vllm/vllm-openai:v0.20.2`.

## Service shape

| Field | Value |
|---|---|
| `hf_model_id` | `google/gemma-4-31B-it` |
| `served_model_name` | `gemma-4-31b` |
| `tensor_parallel` | 1 |
| `data_parallel` | 1 |
| `gpu_count` | 1 |
| `max_model_len` | 16384 |
| `gated` | no (Apache-2.0) |
| Smallest fit | `g7e.2xlarge` (1x Blackwell, 96 GiB) |

ECS-on-EC2 task asks for 4 vCPU + 28 GiB (host margin on a 32 GiB g7e.2xlarge).
Spot first; on-demand fallback.

## Task

Travel-booking JSON extraction from confirmation emails. The benchmark
notebook seeds the endpoint with `SYSTEM_PROMPT` (booking schema) and
runs an async load test against records drawn from
`sample-data/travel/*.jsonl`.

## Usage

```python
# Run from the real-time/ecs/ directory (pyproject sets pythonpath=["src", "."])
from models.gemma_4_31b import SERVICE, SYSTEM_PROMPT, SEED_INPUT
```

See [`notebooks/real-time-ecs.ipynb`](../../notebooks/real-time-ecs.ipynb)
for the full deployment flow.
