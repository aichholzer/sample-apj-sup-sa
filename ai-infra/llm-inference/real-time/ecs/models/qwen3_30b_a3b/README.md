# Qwen3-30B-A3B-Instruct-2507 real-time

Per-model real-time/ecs config for
[`Qwen/Qwen3-30B-A3B-Instruct-2507`](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507),
a 30B-total / 3.3B-active MoE model under Apache-2.0. ~62 GiB BF16 weights
fit on a single g7e.2xlarge (1x Blackwell, 96 GiB).

## Service shape

| Field | Value |
|---|---|
| `hf_model_id` | `Qwen/Qwen3-30B-A3B-Instruct-2507` |
| `served_model_name` | `qwen3-30b-a3b` |
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
from models.qwen3_30b_a3b import SERVICE, SYSTEM_PROMPT, SEED_INPUT
```

See [`notebooks/real-time-ecs.ipynb`](../../notebooks/real-time-ecs.ipynb)
for the full deployment flow.
