# Qwen3-8B real-time

Per-model real-time/ecs config for [`Qwen/Qwen3-8B`](https://huggingface.co/Qwen/Qwen3-8B),
a dense 8B Apache-2.0 model (32K native context). All Qwen3-8B-specific facts
live here; everything else is generic infrastructure in
[`src/vllm_realtime/`](../../src/vllm_realtime/) and
[`cfn/`](../../cfn/).

## Service shape

| Field | Value |
|---|---|
| `hf_model_id` | `Qwen/Qwen3-8B` |
| `served_model_name` | `qwen3-8b` |
| `tensor_parallel` | 1 |
| `data_parallel` | 1 |
| `gpu_count` | 1 |
| `max_model_len` | 16384 |
| `gated` | no |
| Smallest fit | `g6e.xlarge` (1x L40S, 48 GiB) |

ECS-on-EC2 task asks for 4 vCPU + 16 GiB. Single GPU per host: spot first via
the `gpu-spot` capacity provider, on-demand fallback.

## Task

Travel-booking JSON extraction (sample-data domain `travel/`). The benchmark
notebook seeds the endpoint with `SYSTEM_PROMPT` and runs an async load test
against records drawn from `sample-data/travel/*.jsonl`.

## Usage

```python
# Run from the real-time/ecs/ directory (pyproject sets pythonpath=["src", "."])
from models.qwen3_8b import SERVICE, SYSTEM_PROMPT, SEED_INPUT
from vllm_realtime import deploy_stack
```

See [`notebooks/real-time-ecs.ipynb`](../../notebooks/real-time-ecs.ipynb)
for the full deployment flow.
