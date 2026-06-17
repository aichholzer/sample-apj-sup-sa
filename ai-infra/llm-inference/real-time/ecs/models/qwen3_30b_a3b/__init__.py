"""Real-time/ecs config for Qwen3-30B-A3B-Instruct-2507.

30B-total / 3.3B-active MoE; ~62 GiB BF16 fits on a single g7e.2xlarge
(96 GiB Blackwell). The active-experts shape exercises sparse-MoE serving
in the matrix.
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
    "Subject: Eurostar booking confirmation - Reference EUR-2026-04-A8B\n\n"
    "From: noreply@eurostar.com\n\nDear Mr. Andreas Klein,\n\nYour Eurostar "
    "tickets are confirmed. Booking reference: EUR-2026-04-A8B. Outbound: "
    "London St Pancras to Paris Gare du Nord on 2026-04-22 at 09:24, train "
    "9024, Standard Premier. Return: Paris Gare du Nord to London St Pancras "
    "on 2026-04-26 at 18:13, train 9089. Total: 312.40 EUR. Payment: "
    "Mastercard ending 7821. Free cancellation up to 24h before departure."
)

# task_memory_mib < host RAM: cluster places this on g7e.2xlarge (32 GiB
# host RAM); cap container memory at 28672 MiB to leave host headroom.
# health_check_interval_s=120 keeps the total tolerance at
# StartPeriod 300 + 10 retries × 120 s = 25 min, enough for the ~62 GiB
# BF16 download + load on a fresh node.
SERVICE = ModelService(
    model_name="qwen3-30b-a3b",
    hf_model_id="Qwen/Qwen3-30B-A3B-Instruct-2507",
    served_model_name="qwen3-30b-a3b",
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
