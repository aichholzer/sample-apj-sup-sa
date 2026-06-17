# MedGemma-27B real-time

Per-model real-time/ecs config for
[`google/medgemma-27b-text-it`](https://huggingface.co/google/medgemma-27b-text-it),
a 27B medical-tuned Gemma variant under Google's HAI-DEF gated license.
Requires an HF access token (the deployer wires it through Secrets Manager).

## Service shape

| Field | Value |
|---|---|
| `hf_model_id` | `google/medgemma-27b-text-it` |
| `served_model_name` | `medgemma-27b` |
| `tensor_parallel` | 1 |
| `data_parallel` | 1 |
| `gpu_count` | 1 |
| `max_model_len` | 16384 |
| `gated` | yes (HF_TOKEN required) |
| Smallest fit | `g7e.2xlarge` (1x Blackwell, 96 GiB) |

ECS-on-EC2 task asks for 4 vCPU + 32 GiB. Spot first; on-demand fallback.

## Task

Travel-booking detail extraction from confirmation emails. The endpoint is
seeded with the shared `SYSTEM_PROMPT` (booking-record JSON schema) and the
benchmark drives an async load test against records drawn from
`sample-data/travel/*.jsonl`. All text models in this code sample share the
same prompt so that throughput numbers across the matrix are directly
comparable.

## HuggingFace token

Before deploying, set `HF_TOKEN=hf_...` in your environment so the deployer
can upsert it into Secrets Manager:

```python
import os
from vllm_realtime import upsert_secret
upsert_secret(secret_arn=stack.hf_token_secret_arn, value=os.environ["HF_TOKEN"])
```

The secret is injected into the vLLM task definition's `Secrets` block at
task-start time; it never appears in container overrides or in
CloudFormation.

## Usage

```python
# Run from the real-time/ecs/ directory (pyproject sets pythonpath=["src", "."])
from models.medgemma_27b import SERVICE, SYSTEM_PROMPT, SEED_INPUT
```

See [`notebooks/real-time-ecs.ipynb`](../../notebooks/real-time-ecs.ipynb)
for the full deployment flow.
