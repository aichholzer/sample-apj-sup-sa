"""Real-time/ecs config for openai/gpt-oss-20b.

21B-total / 3.6B-active MoE Apache-2.0. Native MXFP4 on Blackwell
(g7e.2xlarge) via vLLM's auto-selected MARLIN_MXFP4 backend; BF16 ~42 GiB
on Ampere fallback. The originally-recommended FlashInfer TRTLLM MXFP4
kernel only supports data-center Blackwell (SM_100, B200), not g7e's
RTX PRO 6000 (SM_120) — vLLM v0.20.2's `select_gpt_oss_mxfp4_moe_backend`
oracle ValueErrors out before the model can load. Letting moe_backend
remain on auto picks Marlin which keeps MXFP4 weights resident at
~13 GiB and supports attention sinks.
"""
from __future__ import annotations

from vllm_realtime import ModelService

SYSTEM_PROMPT = (
    "You are a structured-data extraction assistant. Given a travel booking "
    "confirmation email, extract the booking details and return ONLY a valid "
    "JSON object matching this schema: {\"booking_reference\": string, "
    "\"provider\": string, \"travelers\": [string], \"segments\": "
    "[{\"mode\": string, \"origin\": string, \"destination\": string, "
    "\"depart\": string, \"arrive\": string, \"carrier\": string, "
    "\"class\": string}], \"total_price\": {\"amount\": number, "
    "\"currency\": string}, \"payment_method\": string, "
    "\"cancellation_policy\": string}. Use ISO-8601 for datetimes. Use only "
    "information present in the email. Do not invent fields."
)

SEED_INPUT = (
    "Subject: Your Delta Air Lines Flight is Confirmed - PNR: XYZ789\n\n"
    "From: no_reply@delta.com\n\nDear Ms. Emily Johnson,\n\nWe are pleased "
    "to confirm your round-trip ticket. Booking Reference: XYZ789. Outbound: "
    "New York (LGA) to Los Angeles (LAX) on 2025-04-10 at 08:15 AM, "
    "Flight DL142, Economy. Return: LAX to LGA on 2025-04-17 at 05:30 PM, "
    "Flight DL143. Total: $455.00 USD. Visa ending 4321."
)

SERVICE = ModelService(
    model_name="gpt-oss-20b",
    hf_model_id="openai/gpt-oss-20b",
    served_model_name="gpt-oss-20b",
    tensor_parallel=1,
    data_parallel=1,
    gpu_count=1,
    max_model_len=131072,
    extra_serve_flags=(
        "--tool-call-parser openai "
        "--enable-auto-tool-choice "
        "--reasoning-parser openai_gptoss "
        "--kv-cache-dtype fp8"
    ),
    gated=False,
    task_cpu=4096,
    task_memory_mib=24576,  # 24 GiB on g7e.2xlarge (32 GiB host RAM)
    health_check_interval_s=60,
    health_check_retries=10,
)


__all__ = ["SERVICE", "SEED_INPUT", "SYSTEM_PROMPT"]
