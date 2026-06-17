# Llama-4-Scout-17B-16E real-time

Per-model real-time/ecs config for
[`meta-llama/Llama-4-Scout-17B-16E-Instruct`](https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct),
a 109B-total / 17B-active mixture-of-experts model under Meta's gated Llama-4
license. Requires an HF access token (the deployer wires it through Secrets
Manager).

## Service shape

| Field | Value |
|---|---|
| `hf_model_id` | `meta-llama/Llama-4-Scout-17B-16E-Instruct` |
| `served_model_name` | `llama-4-scout` |
| `tensor_parallel` | 8 |
| `data_parallel` | 1 |
| `gpu_count` | 8 |
| `max_model_len` | 32768 |
| `extra_serve_flags` | `--kv-cache-dtype fp8` |
| `gated` | yes (HF_TOKEN required) |
| Smallest fit | `p4d.24xlarge` (8x A100-40GB) |

ECS-on-EC2 task asks for 8 vCPU + 128 GiB. Spot first via the `gpu-spot`
capacity provider; on-demand fallback. The cluster ASGs default to 4:1
spot:on-demand weighting.

## Why fp8 KV-cache?

Llama-4-Scout has 109B total parameters (16 experts × 17B active). Even with
8x A100-40GB the BF16 weights consume the bulk of GPU memory, leaving little
for KV cache. `--kv-cache-dtype fp8` halves KV memory and lets us reach
`max_model_len=32K` on this hardware. A `p4de.24xlarge` (A100-80GB) wouldn't
need the flag and would fit 64K, but spot availability and price favor p4d
in 2026.

## Task

Travel-booking JSON extraction (sample-data domain `travel/`). The benchmark
notebook seeds the endpoint with `SYSTEM_PROMPT` and runs an async load test
against records drawn from `sample-data/travel/*.jsonl`.

## HuggingFace token

Before deploying, set `HF_TOKEN=hf_...` in your environment so the deployer
can upsert it into Secrets Manager — same pattern as MedGemma above.

## Usage

```python
# Run from the real-time/ecs/ directory (pyproject sets pythonpath=["src", "."])
from models.llama_4_scout_17b import SERVICE, SYSTEM_PROMPT, SEED_INPUT
```

See [`notebooks/real-time-ecs.ipynb`](../../notebooks/real-time-ecs.ipynb)
for the full deployment flow.
