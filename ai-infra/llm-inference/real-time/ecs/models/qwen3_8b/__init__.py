"""Real-time/ecs config for Qwen3-8B."""
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
    "From: no_reply@delta.com\n\nDear Ms. Emily Johnson,\n\nWe are pleased to "
    "confirm your round-trip ticket. Booking Reference: XYZ789. Outbound: New "
    "York (LGA) to Los Angeles (LAX) on 2025-04-10 at 08:15 AM, Flight DL142, "
    "Economy. Return: LAX to LGA on 2025-04-17 at 05:30 PM, Flight DL143. "
    "Total: $455.00 USD. Visa ending 4321."
)

SERVICE = ModelService(
    model_name="qwen3-8b",
    hf_model_id="Qwen/Qwen3-8B",
    served_model_name="qwen3-8b",
    tensor_parallel=1,
    data_parallel=1,
    gpu_count=1,
    max_model_len=16384,
    extra_serve_flags="",
    gated=False,
    task_cpu=4096,
    task_memory_mib=16384,
)


__all__ = ["SERVICE", "SEED_INPUT", "SYSTEM_PROMPT"]
