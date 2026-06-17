"""Real-time/ecs config for Llama 4 Scout 17B-16E."""
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
    "\"cancellation_policy\": string}. Use ISO-8601. Do not invent fields."
)

SEED_INPUT = (
    "Subject: Booking Confirmation - PNR ABC123\n\n"
    "Dear Mr. Lee,\nWe confirm your trip: New York (JFK) -> Tokyo (NRT) on "
    "2025-09-12 09:00, ANA flight NH9. Return NRT -> JFK 2025-09-22 13:45 NH8. "
    "Total: 2,450 USD. Mastercard ending 7788."
)

# 218 GiB BF16 weights take 18-75 min to download from HuggingFace at
# typical 50-200 MiB/s. The default ECS HealthCheck tolerance after
# StartPeriod is 10 * 60s = 10 min, which would kill the task before vLLM
# finishes loading weights. Stretch the interval to 300s so total
# post-StartPeriod tolerance is 10 * 300 = 50 min, plus the 5 min
# StartPeriod = ~55 min total grace.
SERVICE = ModelService(
    model_name="llama-4-scout",
    hf_model_id="meta-llama/Llama-4-Scout-17B-16E-Instruct",
    served_model_name="llama-4-scout",
    tensor_parallel=8,
    data_parallel=1,
    gpu_count=8,
    max_model_len=32768,
    extra_serve_flags="--kv-cache-dtype fp8",
    gated=True,
    task_cpu=8192,
    task_memory_mib=131072,
    health_check_interval_s=300,
)


__all__ = ["SERVICE", "SEED_INPUT", "SYSTEM_PROMPT"]
