"""Static checks for the smoke driver `scripts/smoke_test.py`.

Pins regressions surfaced live during multi-model smoke runs.
"""
from __future__ import annotations

from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "scripts" / "smoke_test.py"


def test_smoke_driver_passes_at_least_300gib_root_volume_for_single_gpu():
    """The cluster instance hosts the entire HF weight cache for the model
    being smoked. When the same cluster gets reused across consecutive
    smokes (e.g. medgemma 27B then qwen3 30B-A3B), 150 GiB is too small —
    the second model's weight write to /var/lib/docker overlayfs fails
    with `IO Error: No space left on device (os error 28)`. The CFN
    default is 300 GiB; the driver must not override it down for the
    single-GPU case.
    """
    src = DRIVER.read_text()
    assert "root_vol = 400 if SERVICE.gpu_count >= 8 else 300" in src, (
        "smoke driver must pass RootVolumeSizeGiB=300 for non-multi-GPU plans "
        "to leave room for multiple model weight caches across consecutive "
        "smokes; lower values caused 'No space left on device' on overlayfs."
    )


def test_smoke_driver_lifts_model_timeout_for_big_moe_models():
    """qwen3-coder-next + llama-4-scout-17b need >3600s vLLM cold-start
    budget even when their gpu_count is < 8 (qwen3-coder-next runs TP=4).
    Audit fix #22 added a big_moe carve-out so the StackCreateComplete
    waiter doesn't trip at exactly 60 min while the engine is still
    finishing torch.compile.
    """
    src = DRIVER.read_text()
    assert "big_moe = SERVICE.model_name in" in src, (
        "smoke driver must carve out big-MoE models to a 7200s timeout — "
        "qwen3-coder-next observed ~64 min cold-start on g6e.12xlarge."
    )
    assert "qwen3-coder-next" in src and "llama-4-scout-17b" in src, (
        "big-MoE timeout carve-out must include both qwen3-coder-next "
        "and llama-4-scout-17b."
    )
    assert "model_timeout = 7200 if (SERVICE.gpu_count >= 8 or big_moe) else 3600" in src, (
        "model_timeout expression must combine the gpu_count and big_moe "
        "branches via OR."
    )
