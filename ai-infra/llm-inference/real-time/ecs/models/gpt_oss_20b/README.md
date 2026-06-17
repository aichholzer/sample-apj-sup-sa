# gpt-oss-20b real-time

Per-model real-time/ecs config for
[`openai/gpt-oss-20b`](https://huggingface.co/openai/gpt-oss-20b)
(Apache-2.0, 21B/3.6B-A MoE, native MXFP4 on Blackwell).

## Service shape

| Field | Value |
|---|---|
| `hf_model_id` | `openai/gpt-oss-20b` |
| `served_model_name` | `gpt-oss-20b` |
| `tensor_parallel` | 1 |
| `data_parallel` | 1 |
| `gpu_count` | 1 |
| `max_model_len` | 131072 |
| `gated` | no (Apache-2.0) |
| Smallest fit | `g7e.2xlarge` (1x Blackwell RTX PRO 6000, 96 GiB) |

## Required env vars

`VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8=1` — selects the MoE FP4/FP8 mixed
kernel on Blackwell so the model resides at ~13 GiB instead of falling
back to BF16 (~42 GiB, won't fit at 131K context). Threaded via
`ModelService.extra_env_vars` and rendered into the CFN parameter
`ExtraEnvVarsJson`; the container entrypoint exports it before
`vllm serve`.

## Required serve flags

* `--tool-call-parser openai`
* `--enable-auto-tool-choice`
* `--reasoning-parser openai_gptoss`
* `--kv-cache-dtype fp8`

## Task

Travel-booking JSON extraction (sample-data domain `travel/`).
