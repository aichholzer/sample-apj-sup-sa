"""Real-time/ecs config for MedGemma-27B."""
from __future__ import annotations

from vllm_realtime import ModelService

SYSTEM_PROMPT = (
    "You are a structured-data extractor. Given a travel booking confirmation "
    "email, return ONLY a valid JSON object matching this schema: "
    "{\"booking_reference\": string, \"traveler\": string, \"origin\": string, "
    "\"destination\": string, \"depart_date\": string, \"return_date\": string|null, "
    "\"total_price\": string, \"currency\": string, \"segments\": array}. Use "
    "ISO-8601 dates (YYYY-MM-DD). Use only information present in the email. "
    "Do not invent fields."
)

SEED_INPUT = (
    "Subject: Your booking is confirmed — PNR ABC123\n"
    "From: no-reply@example-air.com\n\n"
    "Dear Jordan Lee,\n"
    "Booking reference: ABC123\n"
    "From: San Francisco (SFO)  To: Tokyo (HND)\n"
    "Outbound: 2026-08-12 EX 0421 Economy\n"
    "Return:   2026-08-26 EX 0422 Economy\n"
    "Total fare: USD 1,289.50 (paid with **** 4321)"
)

# task_memory_mib must be strictly less than the cluster instance's total
# physical RAM (after the ECS agent's reserved 512 MiB and kernel overhead).
# medgemma-27b runs on g7e.2xlarge (gpu_count=1, 32 GiB total RAM); a
# 32-GiB task can never be scheduled, so cap at 28 GiB to leave at least
# 3+ GiB host margin for kernel, ECS agent, container runtime.
# health_check_interval_s=120 (default 60) makes the total tolerance =
# StartPeriod 300 + Retries 10 * Interval 120 = 1500s = 25 min, enough for
# the ~14-min cold start (HF download + 11-shard load + warmup).
SERVICE = ModelService(
    model_name="medgemma-27b",
    hf_model_id="google/medgemma-27b-text-it",
    served_model_name="medgemma-27b",
    tensor_parallel=1,
    data_parallel=1,
    gpu_count=1,
    max_model_len=16384,
    extra_serve_flags="",
    gated=True,
    task_cpu=4096,
    task_memory_mib=28672,
    health_check_interval_s=120,
    health_check_retries=10,
)


__all__ = ["SERVICE", "SEED_INPUT", "SYSTEM_PROMPT"]
