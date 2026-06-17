"""Real-time/ecs config for Mistral-Small-3.2-24B-Instruct-2506.

Apache-2.0 dense 24B (~55 GiB BF16). Fits on a single g7e.2xlarge (96 GiB
Blackwell). Mistral-Small ships only the Mistral-native artefact (no HF
format), so vLLM needs the three ``--*-mode mistral`` flags or it fails
with a tokenizer/config-format error.

55 GiB BF16 weights take longer to download + load than the 15-min default
ECS health budget allows, same fix as MedGemma-27B and Qwen3-30B-A3B.
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
    "Subject: Booking Confirmation - Reference DLT-2026-04-721\n\n"
    "From: no_reply@delta.com\n\nDear Ms. Emily Johnson,\n\nWe confirm your "
    "round-trip ticket. Booking reference: DLT-2026-04-721. Outbound: "
    "New York (LGA) to Los Angeles (LAX) on 2026-04-10 at 08:15, Flight "
    "DL142, Economy. Return: Los Angeles (LAX) to New York (LGA) on "
    "2026-04-17 at 17:30, Flight DL143, Economy. Total: 455.00 USD. "
    "Payment: Visa ending 4321. Cancellations 14+ days before incur 50 USD."
)

SERVICE = ModelService(
    model_name="mistral-small-3-2-24b",
    hf_model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    served_model_name="mistral-small-3-2-24b",
    tensor_parallel=1,
    data_parallel=1,
    gpu_count=1,
    max_model_len=16384,
    # Mistral-Small ships only the Mistral-native artefact; without these
    # three flags vLLM fails to load with a tokenizer/config-format error.
    extra_serve_flags=(
        "--tokenizer-mode mistral "
        "--config-format mistral "
        "--load-format mistral"
    ),
    gated=False,
    task_cpu=4096,
    task_memory_mib=28672,
    health_check_interval_s=120,
    health_check_retries=10,
)


__all__ = ["SERVICE", "SEED_INPUT", "SYSTEM_PROMPT"]
