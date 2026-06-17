"""Async load test against the LiteLLM router fronting a vLLM model service.

Designed for a notebook: small dependency-free interface that returns a tidy
DataFrame-friendly summary.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

import httpx


@dataclass
class LoadTestResult:
    n_requests: int
    n_success: int
    n_failed: int
    elapsed_s: float
    p50_latency_s: float
    p95_latency_s: float
    p99_latency_s: float
    output_tokens: int
    output_throughput_tok_s: float

    def as_dict(self) -> dict[str, float]:
        return {
            "n_requests": self.n_requests,
            "n_success": self.n_success,
            "n_failed": self.n_failed,
            "elapsed_s": self.elapsed_s,
            "p50_latency_s": self.p50_latency_s,
            "p95_latency_s": self.p95_latency_s,
            "p99_latency_s": self.p99_latency_s,
            "output_tokens": self.output_tokens,
            "output_throughput_tok_s": self.output_throughput_tok_s,
        }


def smoke_test_endpoint(
    *,
    base_url: str,
    api_key: str,
    served_model_name: str,
    system_prompt: str,
    user_input: str,
    timeout_s: int = 120,
    max_tokens: int = 256,
) -> dict:
    """Sync single-request smoke check. Returns the parsed JSON response."""
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    body = {
        "model": served_model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    with httpx.Client(timeout=timeout_s) as cli:
        r = cli.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def _one(client: httpx.AsyncClient, url: str, body: dict,
               api_key: str) -> tuple[float, int, bool]:
    t0 = time.perf_counter()
    try:
        r = await client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
        )
        if r.status_code != 200:
            return time.perf_counter() - t0, 0, False
        data = r.json()
        out_tokens = data.get("usage", {}).get("completion_tokens", 0)
        return time.perf_counter() - t0, int(out_tokens), True
    except httpx.HTTPError:
        return time.perf_counter() - t0, 0, False


async def async_load_test(
    *,
    base_url: str,
    api_key: str,
    served_model_name: str,
    system_prompt: str,
    user_inputs: Sequence[str],
    concurrency: int = 16,
    max_tokens: int = 256,
    timeout_s: int = 1800,
) -> LoadTestResult:
    """Issue len(user_inputs) requests with bounded concurrency. Aggregate.

    The default ``timeout_s`` matches LiteLLM's *retry-amplified* total
    per-request budget (``router_settings.timeout × (1 + num_retries)``
    = 600 × 3 = 1800 in 30-litellm-router.yaml). This keeps the load
    test from misclassifying healthy long-tail requests as failures
    while LiteLLM is still on its retry budget — the same retry-
    amplified shape that bug #18 fixed for ALB idle_timeout / TG drain.
    """
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    out_tokens: list[int] = []
    successes = 0
    failures = 0

    async def _run(text: str, client: httpx.AsyncClient) -> None:
        nonlocal successes, failures
        body = {
            "model": served_model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        async with sem:
            lat, tok, ok = await _one(client, url, body, api_key)
        latencies.append(lat)
        out_tokens.append(tok)
        if ok:
            successes += 1
        else:
            failures += 1

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        await asyncio.gather(*[_run(t, client) for t in user_inputs])
    elapsed = time.perf_counter() - t0

    if not latencies:
        return LoadTestResult(0, 0, 0, elapsed, 0, 0, 0, 0, 0)

    sorted_lat = sorted(latencies)
    n = len(sorted_lat)

    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return sorted_lat[idx]

    total_tokens = sum(out_tokens)
    return LoadTestResult(
        n_requests=n,
        n_success=successes,
        n_failed=failures,
        elapsed_s=elapsed,
        p50_latency_s=pct(0.50),
        p95_latency_s=pct(0.95),
        p99_latency_s=pct(0.99),
        output_tokens=total_tokens,
        output_throughput_tok_s=total_tokens / elapsed if elapsed > 0 else 0,
    )


__all__ = ["LoadTestResult", "async_load_test", "smoke_test_endpoint"]
