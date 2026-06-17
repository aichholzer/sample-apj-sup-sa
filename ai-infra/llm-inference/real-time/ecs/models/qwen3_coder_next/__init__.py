"""Real-time/ecs config for Qwen/Qwen3-Coder-Next.

80B-total / 3B-active MoE Apache-2.0 coding model. The qwen3_next hybrid
architecture (Gated DeltaNet + Gated Attention) requires vLLM >= 0.15.0;
the repo pin (v0.20.2) covers it.

Smallest fit: g6e.12xlarge (4xL40S, FP8 quant) with TP=4. The model does
NOT emit `<think>`; recommended sampling is temperature=1.0, top_p=0.95,
top_k=40.
"""
from __future__ import annotations

from vllm_realtime import ModelService

SYSTEM_PROMPT = (
    "You are an expert software engineer. Given a travel booking "
    "confirmation email, return ONLY a single Python code block (no prose) "
    "that defines: (1) a `Booking` @dataclass mirroring the booking schema, "
    "(2) `Segment` and `TotalPrice` @dataclasses, and (3) a "
    "`parse_email(text: str) -> Booking` function that returns a fully-"
    "populated Booking from the email text. Use only `re`, `dataclasses`, "
    "and `datetime`. Datetimes must be `datetime.datetime` ISO-parsed. "
    "Return None for fields not present; never invent values."
)

SEED_INPUT = (
    "Subject: Your Delta Air Lines Flight is Confirmed - PNR: XYZ789\n\n"
    "From: no_reply@delta.com\n\nDear Ms. Emily Johnson,\n\nWe are pleased "
    "to confirm your round-trip ticket. Booking Reference: XYZ789. Outbound: "
    "New York (LGA) to Los Angeles (LAX) on 2025-04-10 at 08:15 AM, "
    "Flight DL142, Economy. Total: $455.00 USD. Visa ending 4321."
)

# 80B BF16 weights ~160 GiB; FP8 quant cold-start takes ~25-40 min on
# 4xL40S (HF download + on-the-fly quantize + warmup). Bump health check
# to 300s * 10 = 50 min total post-StartPeriod tolerance, same shape as
# Llama-4-Scout.
SERVICE = ModelService(
    model_name="qwen3-coder-next",
    hf_model_id="Qwen/Qwen3-Coder-Next",
    served_model_name="qwen3-coder-next",
    tensor_parallel=4,
    data_parallel=1,
    gpu_count=4,
    max_model_len=32768,
    extra_serve_flags=(
        "--enable-auto-tool-choice "
        "--tool-call-parser qwen3_coder "
        "--quantization fp8"
    ),
    gated=False,
    task_cpu=16384,
    task_memory_mib=98304,  # 96 GiB (g6e.12xlarge has 192 GiB host RAM)
    health_check_interval_s=300,
    health_check_retries=10,
)


__all__ = ["SERVICE", "SEED_INPUT", "SYSTEM_PROMPT"]
