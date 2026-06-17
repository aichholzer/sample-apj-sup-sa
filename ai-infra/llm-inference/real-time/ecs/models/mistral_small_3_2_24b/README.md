# Mistral-Small-3.2-24B real-time

Per-model real-time/ecs config for
[`mistralai/Mistral-Small-3.2-24B-Instruct-2506`](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506),
a 24B Apache-2.0 dense model with 128K context. Mistral-Small ships only
the Mistral-native artefact (no HF format), so the deployer passes the
three `--*-mode mistral` flags or vLLM fails to load.

## Service shape

| Field | Value |
|---|---|
| `hf_model_id` | `mistralai/Mistral-Small-3.2-24B-Instruct-2506` |
| `served_model_name` | `mistral-small-3-2-24b` |
| `tensor_parallel` | 1 |
| `data_parallel` | 1 |
| `gpu_count` | 1 |
| `max_model_len` | 16384 |
| `extra_serve_flags` | `--tokenizer-mode mistral --config-format mistral --load-format mistral` |
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
from models.mistral_small_3_2_24b import SERVICE, SYSTEM_PROMPT, SEED_INPUT
```

See [`notebooks/real-time-ecs.ipynb`](../../notebooks/real-time-ecs.ipynb)
for the full deployment flow.
