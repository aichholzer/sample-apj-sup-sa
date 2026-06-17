"""Unit tests for vllm_realtime.ModelService parameter generation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "models"))

from vllm_realtime import ModelService  # noqa: E402


def _params_to_dict(params: list[dict[str, str]]) -> dict[str, str]:
    return {p["ParameterKey"]: p["ParameterValue"] for p in params}


def test_default_stack_names():
    s = ModelService(
        model_name="qwen3-8b",
        hf_model_id="Qwen/Qwen3-8B",
        served_model_name="qwen3-8b",
    )
    assert s.model_stack_name == "llm-rt-vllm-qwen3-8b"
    assert s.router_stack_name == "llm-rt-litellm-qwen3-8b"


def test_model_parameters_required_keys():
    s = ModelService(
        model_name="qwen3-8b",
        hf_model_id="Qwen/Qwen3-8B",
        served_model_name="qwen3-8b",
        tensor_parallel=2,
        data_parallel=1,
        gpu_count=2,
        max_model_len=32768,
        extra_serve_flags="--enforce-eager",
    )
    params = _params_to_dict(s.model_parameters(  # nosec B106
        resource_prefix="llm-inference-rt",
        vllm_image="vllm/vllm-openai:v0.10.2",
        hf_token_secret_arn="arn:aws:secretsmanager:us-west-2:111:secret:hf",
        api_key_secret_arn="arn:aws:secretsmanager:us-west-2:111:secret:api",
    ))
    assert params["ModelName"] == "qwen3-8b"
    assert params["HfModelId"] == "Qwen/Qwen3-8B"
    assert params["ServedModelName"] == "qwen3-8b"
    assert params["TensorParallel"] == "2"
    assert params["DataParallel"] == "1"
    assert params["GpuCount"] == "2"
    assert params["MaxModelLen"] == "32768"
    assert params["ExtraServeFlags"] == "--enforce-eager"
    assert params["VllmApiKeySecretArn"].endswith(":secret:api")
    # Non-gated model: HfTokenSecretArn must NOT be passed (template default applies)
    assert "HfTokenSecretArn" not in params


def test_model_parameters_gated_includes_hf_token():
    s = ModelService(
        model_name="medgemma-27b",
        hf_model_id="google/medgemma-27b-text-it",
        served_model_name="medgemma-27b",
        gated=True,
    )
    params = _params_to_dict(s.model_parameters(  # nosec B106
        resource_prefix="llm-inference-rt",
        vllm_image="vllm/vllm-openai:v0.10.2",
        hf_token_secret_arn="arn:aws:secretsmanager:us-west-2:111:secret:hf",
        api_key_secret_arn="arn:aws:secretsmanager:us-west-2:111:secret:api",
    ))
    assert params["HfTokenSecretArn"].endswith(":secret:hf")


def test_router_parameters():
    s = ModelService(
        model_name="qwen3-8b",
        hf_model_id="Qwen/Qwen3-8B",
        served_model_name="qwen3-8b",
    )
    params = _params_to_dict(s.router_parameters(  # nosec B106
        resource_prefix="llm-inference-rt",
        litellm_master_key_secret_arn="arn:aws:secretsmanager:us-west-2:111:secret:m",
        api_key_secret_arn="arn:aws:secretsmanager:us-west-2:111:secret:api",
        listener_priority=42,
        path_prefix="/qwen3-8b",
    ))
    assert params["ResourcePrefix"] == "llm-inference-rt"
    assert params["ModelName"] == "qwen3-8b"
    assert params["AlbListenerPriority"] == "42"
    assert params["PathPrefix"] == "/qwen3-8b"


def test_router_default_litellm_image():
    s = ModelService(
        model_name="x",
        hf_model_id="x/x",
        served_model_name="x",
    )
    params = _params_to_dict(s.router_parameters(  # nosec B106
        resource_prefix="rt",
        litellm_master_key_secret_arn="a",
        api_key_secret_arn="b",
    ))
    assert params["LiteLLMImage"] == "ghcr.io/berriai/litellm:main-stable"


def test_router_default_path_prefix_is_wildcard():
    """Bare '/' literally matches only the root path on ALB; default must
    be '/*' so OpenAI-style endpoints (/v1/chat/completions, /v1/models, ...)
    are routed correctly out of the box.
    """
    s = ModelService(
        model_name="x",
        hf_model_id="x/x",
        served_model_name="x",
    )
    params = _params_to_dict(s.router_parameters(  # nosec B106
        resource_prefix="rt",
        litellm_master_key_secret_arn="a",
        api_key_secret_arn="b",
    ))
    assert params["PathPrefix"] == "/*"


def test_router_default_listener_protocol_is_https():
    """The default targets the production networking template (which only
    ships an HTTPS listener). Cert-less dev deploys against
    ``00-networking-dev.yaml`` must explicitly pass
    ``listener_protocol='HTTP'``."""
    s = ModelService(
        model_name="x",
        hf_model_id="x/x",
        served_model_name="x",
    )
    params = _params_to_dict(s.router_parameters(  # nosec B106
        resource_prefix="rt",
        litellm_master_key_secret_arn="a",
        api_key_secret_arn="b",
    ))
    assert params["ListenerProtocol"] == "HTTPS"


def test_router_listener_protocol_https_passes_through():
    s = ModelService(
        model_name="x",
        hf_model_id="x/x",
        served_model_name="x",
    )
    params = _params_to_dict(s.router_parameters(  # nosec B106
        resource_prefix="rt",
        litellm_master_key_secret_arn="a",
        api_key_secret_arn="b",
        listener_protocol="HTTPS",
    ))
    assert params["ListenerProtocol"] == "HTTPS"


def test_router_listener_protocol_rejects_invalid_value():
    s = ModelService(
        model_name="x",
        hf_model_id="x/x",
        served_model_name="x",
    )
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        s.router_parameters(  # nosec B106
            resource_prefix="rt",
            litellm_master_key_secret_arn="a",
            api_key_secret_arn="b",
            listener_protocol="ftp",
        )


def test_per_model_configs_load():
    """The three shipped per-model packages must import cleanly with valid SERVICE."""
    import qwen3_8b
    import medgemma_27b
    import llama_4_scout_17b

    assert qwen3_8b.SERVICE.model_name == "qwen3-8b"
    assert qwen3_8b.SERVICE.gated is False
    assert qwen3_8b.SYSTEM_PROMPT and qwen3_8b.SEED_INPUT

    assert medgemma_27b.SERVICE.model_name == "medgemma-27b"
    assert medgemma_27b.SERVICE.gated is True
    # All text models use the same travel-booking extraction prompt for
    # matrix-comparable throughput numbers.
    assert "booking" in medgemma_27b.SYSTEM_PROMPT.lower()

    assert llama_4_scout_17b.SERVICE.gpu_count == 8
    assert llama_4_scout_17b.SERVICE.tensor_parallel == 8
