"""Smoke-import every per-model real-time config.

The matrix requires real-time/ECS
coverage for ALL 6 models, not just the 3 originally scoped in
deployment for every model in the lineup.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

from vllm_realtime import ModelService

_MODELS: list[str] = [
    "models.qwen3_8b",
    "models.qwen3_30b_a3b",
    "models.mistral_small_3_2_24b",
    "models.gemma_4_31b",
    "models.medgemma_27b",
    "models.llama_4_scout_17b",
    # Additional models added later:
    "models.gpt_oss_20b",
    "models.qwen3_coder_next",
    "models.qwen3_vl_30b_a3b",
]


@pytest.mark.parametrize("package", _MODELS)
def test_realtime_model_package_imports(package: str) -> None:
    pkg = importlib.import_module(package)
    assert isinstance(pkg.SERVICE, ModelService), \
        "SERVICE must be a ModelService instance"
    assert pkg.SERVICE.hf_model_id
    assert pkg.SERVICE.served_model_name
    assert pkg.SERVICE.gpu_count >= 1
    assert pkg.SERVICE.task_cpu >= 1024
    assert pkg.SERVICE.task_memory_mib >= 4096
    assert isinstance(pkg.SYSTEM_PROMPT, str) and pkg.SYSTEM_PROMPT.strip()
    assert isinstance(pkg.SEED_INPUT, str) and pkg.SEED_INPUT.strip()


def test_llama_4_scout_uses_8_gpus() -> None:
    """Llama-4-Scout needs all 8 GPUs of a p4d/p4de host."""
    pkg = importlib.import_module("models.llama_4_scout_17b")
    assert pkg.SERVICE.tensor_parallel == 8
    assert pkg.SERVICE.gpu_count == 8


def test_medgemma_marks_gated() -> None:
    """MedGemma is HAI-DEF gated — must require HF_TOKEN."""
    pkg = importlib.import_module("models.medgemma_27b")
    assert pkg.SERVICE.gated is True


@pytest.mark.parametrize("package", _MODELS)
def test_each_model_has_readme(package: str) -> None:
    """Every per-model dir must ship a README.md (doc convention)."""
    pkg_dir = importlib.util.find_spec(package).submodule_search_locations[0]
    readme = Path(pkg_dir) / "README.md"
    assert readme.exists(), f"{package}: README.md missing at {readme}"
    assert readme.stat().st_size > 200, f"{package}: README.md too small"


# Notebook (`real-time/ecs/scripts/build_notebook.py::_CLUSTER_INSTANCE_BY_GPU`)
# maps SERVICE.gpu_count -> cluster instance type. Total physical RAM per
# instance type below; the ECS agent reserves 512 MiB and the kernel/runtime
# cost another ~1-2 GiB, so leave at least a ~2 GiB host margin.
#
# Source: AWS instance type RAM specs.
_CLUSTER_HOST_RAM_MIB = {
    1: 32768,    # g7e.2xlarge: 32 GiB
    4: 393216,   # g6e.12xlarge: 384 GiB
    8: 1179648,  # p4d.24xlarge: 1152 GiB
}


@pytest.mark.parametrize("package", _MODELS)
def test_task_memory_fits_on_designated_cluster_host(package: str) -> None:
    """task_memory_mib must be strictly less than the cluster instance's
    total physical RAM. ECS rejects tasks requesting more memory than any
    container instance in the cluster offers — they sit in PENDING forever
    and managed scaling spins up more instances that *also* can't fit the
    task, so the deploy completes (CFN says CREATE_COMPLETE) but no GPU
    serves traffic.

    Tightest case here was medgemma-27b: gpu_count=1 -> g7e.2xlarge (32 GiB),
    declared task_memory_mib=32768 == host RAM. ECS would queue the task
    forever. Cap at host_ram - 4 GiB so the kernel, ECS agent, container
    runtime, and CloudWatch agent all have headroom.
    """
    pkg = importlib.import_module(package)
    svc = pkg.SERVICE
    host_ram = _CLUSTER_HOST_RAM_MIB.get(svc.gpu_count)
    assert host_ram is not None, (
        f"{package}: gpu_count={svc.gpu_count} has no cluster instance mapping; "
        f"either add it to _CLUSTER_HOST_RAM_MIB and to "
        f"build_notebook.py::_CLUSTER_INSTANCE_BY_GPU, or set gpu_count to "
        f"a mapped value."
    )
    margin_mib = 4096
    assert svc.task_memory_mib + margin_mib <= host_ram, (
        f"{package}: task_memory_mib={svc.task_memory_mib} on a "
        f"{host_ram}-MiB host leaves <{margin_mib} MiB host margin — "
        f"tasks will queue PENDING. Drop task_memory_mib to "
        f"{host_ram - margin_mib} or lower."
    )


# Approx HuggingFace BF16 weight sizes (GiB). Used to compute the minimum
# ECS HealthCheck grace period each model needs after the container starts.
# At a conservative HF-to-EC2 download speed of 50 MiB/s these convert to
# minutes, then we add 5 min of buffer for image pull + warmup.
_WEIGHT_GIB_BY_PACKAGE = {
    "models.qwen3_8b": 17.0,
    "models.qwen3_30b_a3b": 62.0,
    "models.mistral_small_3_2_24b": 55.0,
    "models.gemma_4_31b": 64.0,
    "models.medgemma_27b": 55.0,
    "models.llama_4_scout_17b": 218.0,
    # Iter-4 additions:
    # gpt-oss-20b BF16 ~42 GiB on Ampere; on Blackwell native MXFP4 is
    # ~13 GiB but the upstream HF repo ships BF16 source weights, so the
    # download is ~42 GiB regardless.
    "models.gpt_oss_20b": 42.0,
    # 80B BF16 ~160 GiB. FP8 quant happens after download.
    "models.qwen3_coder_next": 160.0,
    # 30B BF16 + ViT.
    "models.qwen3_vl_30b_a3b": 62.0,
}
# Conservative HF→EC2 throughput floor. Real-world is 100-500 MiB/s
# depending on region and instance NIC bandwidth; we budget at the lower
# end of that band so the grace period covers a slow-but-not-pathological
# transfer. Going lower (e.g. 50 MiB/s) is theoretically safer but exceeds
# what the ECS HealthCheck Interval cap (300s) can compensate for on the
# largest model — the test would always fail for Llama-4-Scout regardless
# of how the SERVICE is configured.
_HF_DL_FLOOR_MIB_S = 100.0


@pytest.mark.parametrize("package", _MODELS)
def test_health_check_grace_period_covers_weight_download(package: str) -> None:
    """ECS kills a container if /health stays unhealthy after StartPeriod
    expires AND `Retries` consecutive checks fail. The grace period after
    StartPeriod is therefore Retries * Interval seconds.

    The CFN template's StartPeriod is fixed at 300s (the ECS hard cap).
    Any model whose weight download alone exceeds (300 + Retries * Interval)
    will be killed before vLLM finishes loading. Llama-4-Scout (~218 GiB
    BF16) at a conservative 50 MiB/s downloads in ~75 min — the default
    grace period of 300 + 10*60 = 900s = 15 min would kill it ~60 min early.

    This test ensures every model's HealthCheck grace period covers the
    minimum-throughput weight-download time plus a 5-minute warmup buffer.
    """
    pkg = importlib.import_module(package)
    svc = pkg.SERVICE
    weight_gib = _WEIGHT_GIB_BY_PACKAGE.get(package)
    assert weight_gib is not None, (
        f"{package}: add to _WEIGHT_GIB_BY_PACKAGE so the health-check "
        f"budget can be validated."
    )
    download_seconds = (weight_gib * 1024) / _HF_DL_FLOOR_MIB_S
    warmup_buffer_s = 300  # image pull + CUDA graph capture + container reg.
    needed_s = download_seconds + warmup_buffer_s

    start_period_s = 300  # CFN-pinned (ECS hard cap)
    grace_s = start_period_s + svc.health_check_retries * svc.health_check_interval_s

    assert grace_s >= needed_s, (
        f"{package}: HealthCheck grace = {grace_s}s "
        f"(StartPeriod 300 + retries {svc.health_check_retries} * "
        f"interval {svc.health_check_interval_s}s) but weight download "
        f"alone needs >= {needed_s:.0f}s at {_HF_DL_FLOOR_MIB_S} MiB/s. "
        f"Bump health_check_interval_s on this model's SERVICE so the "
        f"product covers the download budget."
    )
