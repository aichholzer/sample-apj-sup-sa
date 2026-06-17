"""Real-time/ecs config for Gemma-4-31B-it.

Apache-2.0 dense 31B (~64 GiB BF16). Fits on a single g7e.2xlarge (96 GiB
Blackwell). Gemma 4 (released Apr 2026) requires vLLM >= 0.11 (the smoke
driver pins v0.20.2). 64 GiB BF16 weights take longer to download + load
than the 15-min default ECS health budget allows, same fix as MedGemma-27B
and Qwen3-30B-A3B.
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
    "Subject: Hilton booking confirmation - REF HIL-2026-05-9F4\n\n"
    "From: noreply@hilton.com\n\nDear Mr. Daniel Park,\n\nYour hotel stay is "
    "confirmed. Booking reference: HIL-2026-05-9F4. Hotel: Hilton San "
    "Francisco Union Square. Check-in: 2026-05-22 15:00. Check-out: "
    "2026-05-25 11:00. Room: King Suite. Total: 1,140.00 USD. Payment: "
    "American Express ending 3007. Free cancellation up to 48h before check-in."
)

SERVICE = ModelService(
    model_name="gemma-4-31b",
    hf_model_id="google/gemma-4-31B-it",
    served_model_name="gemma-4-31b",
    tensor_parallel=1,
    data_parallel=1,
    gpu_count=1,
    max_model_len=16384,
    extra_serve_flags="",
    gated=False,
    task_cpu=4096,
    task_memory_mib=28672,
    health_check_interval_s=120,
    health_check_retries=10,
)


__all__ = ["SERVICE", "SEED_INPUT", "SYSTEM_PROMPT"]
