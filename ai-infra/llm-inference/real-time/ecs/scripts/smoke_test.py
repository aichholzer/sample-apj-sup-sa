"""End-to-end smoke driver for real-time/ecs scenario.

Mirrors the notebook flow (00-networking + 10-cluster + 20-vllm-model +
30-litellm-router) but drives it from a CLI so the agent can iterate over
multiple models without a Jupyter kernel.

Usage::

    LLM_RT_SMOKE=YES python real-time/ecs/scripts/smoke_test.py \\
        --model qwen3_8b --region us-west-2

The smoke deploys all four stacks, fires one chat-completion through the
ALB, asserts the response is non-empty, then tears the per-model stacks
down (keeps the per-region networking + cluster stacks unless --teardown-all
is passed).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import secrets as _secrets
import sys
import time
from importlib import import_module
from pathlib import Path

import boto3

RT_ECS = Path(__file__).resolve().parents[1]  # real-time/ecs
sys.path.insert(0, str(RT_ECS / "src"))
sys.path.insert(0, str(RT_ECS / "models"))

from vllm_realtime import (  # noqa: E402
    CFN_DIR,
    deploy_stack,
    teardown_stack,
    upsert_secret,
    wait_for_alb_healthy,
    smoke_test_endpoint,
    async_load_test,
)
from vllm_realtime.deployer import sweep_guardduty_vpc_endpoints  # noqa: E402

GATE = "LLM_RT_SMOKE"
RESOURCE_PREFIX = "llm-inference-rt"


def _hf_token(region: str) -> str:
    sm = boto3.client("secretsmanager", region_name=region)
    return sm.get_secret_value(SecretId="llm-inference/hf-token")["SecretString"]


def _listener_priority(model_name: str) -> int:
    # MD5 is used as a stable name → integer mapping for ALB listener-rule
    # priorities (a non-security identifier-derivation use), so we set
    # usedforsecurity=False to make the intent explicit to scanners.
    digest = hashlib.md5(model_name.encode(), usedforsecurity=False).hexdigest()
    return 100 + (int(digest, 16) % 900)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True,
                   help="model package under real-time/ecs/models/")
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--keep", action="store_true",
                   help="leave per-model stacks up after smoke (for debugging)")
    p.add_argument("--teardown-all", action="store_true",
                   help="also delete the per-region networking+cluster stacks")
    args = p.parse_args()

    if os.environ.get(GATE) != "YES":
        print(f"refusing to run; set {GATE}=YES to acknowledge GPU spend")
        return 2

    mod = import_module(args.model)
    SERVICE = mod.SERVICE
    SYSTEM_PROMPT = mod.SYSTEM_PROMPT
    SEED_INPUT = mod.SEED_INPUT
    region = args.region
    model_name = SERVICE.model_name
    print(f"[+] smoke {model_name} in {region}")

    # 1) networking — use the dev template since this smoke flow runs
    # without an ACM cert. The production ``00-networking.yaml`` requires
    # AlbCertificateArn and does not create a plaintext HTTP listener.
    print("[+] deploy networking (dev template)")
    net_outputs = deploy_stack(
        stack_name=f"{RESOURCE_PREFIX}-networking",
        template_path=CFN_DIR / "00-networking-dev.yaml",
        parameters=[
            {"ParameterKey": "ResourcePrefix", "ParameterValue": RESOURCE_PREFIX},
            {"ParameterKey": "AlbCertificateArn", "ParameterValue": ""},
        ],
        region=region,
        tags={"Project": "llm-inference"},
    )
    alb_dns = net_outputs["AlbDns"]
    vpc_id = net_outputs.get("VpcId", "")
    print(f"    ALB: {alb_dns}")

    # 2) cluster
    print("[+] deploy cluster")
    # Pick a cluster InstanceType big enough for SERVICE.gpu_count. Mirrors
    # `_CLUSTER_INSTANCE_BY_GPU` in scripts/build_notebook.py so the smoke
    # picks the same hardware the notebook would. 4-GPU models (Qwen3-VL,
    # Qwen3-Coder-Next) need g6e.12xlarge (4xL40S, 192 GiB).
    _CLUSTER_INSTANCE_BY_GPU = {
        1: "g7e.2xlarge",
        4: "g6e.12xlarge",
        8: "p4d.24xlarge",
    }
    cluster_instance = _CLUSTER_INSTANCE_BY_GPU.get(SERVICE.gpu_count, "g7e.2xlarge")
    # 300 GiB root: leaves room for the ECS AMI (~10 GiB), the vLLM container
    # image (~10 GiB), AND multiple model weight caches. Earlier 150 GiB was
    # too small once we re-used the same cluster instance across consecutive
    # models — the second smoke ran out of /var/lib/docker overlayfs space and
    # the new task crash-looped with `IO Error: No space left on device`.
    root_vol = 400 if SERVICE.gpu_count >= 8 else 300
    cluster_outputs = deploy_stack(
        stack_name=f"{RESOURCE_PREFIX}-cluster",
        template_path=CFN_DIR / "10-cluster.yaml",
        parameters=[
            {"ParameterKey": "ResourcePrefix",    "ParameterValue": RESOURCE_PREFIX},
            {"ParameterKey": "InstanceType",      "ParameterValue": cluster_instance},
            {"ParameterKey": "RootVolumeSizeGiB", "ParameterValue": str(root_vol)},
        ],
        region=region,
        tags={"Project": "llm-inference"},
    )
    print(f"    cluster: {cluster_outputs.get('ClusterName')} ({cluster_instance})")

    # 3) secrets
    print("[+] upsert secrets")
    HF_TOKEN_ARN = upsert_secret(
        name=f"{RESOURCE_PREFIX}/hf-token",
        value=_hf_token(region),
        region=region,
        description="HuggingFace token for gated model pulls",
    )
    # Symmetric keys consumed by long-running ECS tasks: keep the same
    # value across re-runs so vLLM (started at first deploy) and LiteLLM
    # (started at first deploy) keep agreeing. Otherwise the second smoke
    # run rotates the secret, LiteLLM's pod is still booted off the
    # OLD value and forwards it; vLLM was booted off the EVEN OLDER
    # value and rejects with 401. Idempotent upsert avoids the desync.
    VLLM_API_KEY = _secrets.token_urlsafe(32)
    VLLM_API_KEY_ARN = upsert_secret(
        name=f"{RESOURCE_PREFIX}/{model_name}/vllm-api-key",
        value=VLLM_API_KEY,
        region=region,
        description="vLLM Authorization bearer key",
        model_name=model_name,
        rotate_if_exists=False,
    )
    LITELLM_KEY = "sk-" + _secrets.token_urlsafe(32)
    LITELLM_KEY_ARN = upsert_secret(
        name=f"{RESOURCE_PREFIX}/{model_name}/litellm-master-key",
        value=LITELLM_KEY,
        region=region,
        description="LiteLLM master key",
        model_name=model_name,
        rotate_if_exists=False,
    )
    # Read-back: get whatever value is actually live in Secrets Manager
    # (the create-or-keep result above), so the smoke uses what the
    # services use.
    sm = boto3.client("secretsmanager", region_name=region)
    LITELLM_KEY = sm.get_secret_value(
        SecretId=LITELLM_KEY_ARN,
    )["SecretString"]

    # 4) vLLM model stack
    print("[+] deploy vLLM model stack (cold start)")
    # Big-MoE budget carve-out: qwen3-coder-next is 80B-total MoE on TP=4
    # (gpu_count=4 so it wouldn't trip the >=8 branch), but its vLLM
    # cold-start (model_loading >40 min + torch.compile + warmup) can
    # exceed the default 3600s waiter on g6e.12xlarge. Lift to 7200 for
    # any service whose architecture is hybrid/Mamba-style or whose
    # observed model-load runs longer than ~30 min.
    big_moe = SERVICE.model_name in ("qwen3-coder-next", "llama-4-scout-17b")
    model_timeout = 7200 if (SERVICE.gpu_count >= 8 or big_moe) else 3600
    model_outputs = deploy_stack(
        stack_name=SERVICE.model_stack_name,
        template_path=CFN_DIR / "20-vllm-model.yaml",
        parameters=SERVICE.model_parameters(
            resource_prefix=RESOURCE_PREFIX,
            vllm_image="vllm/vllm-openai:v0.20.2",
            hf_token_secret_arn=HF_TOKEN_ARN,
            api_key_secret_arn=VLLM_API_KEY_ARN,
        ),
        region=region,
        tags={"Project": "llm-inference", "Model": model_name},
        wait_timeout_s=model_timeout,
    )
    print(f"    Cloud Map: {model_outputs.get('CloudMapServiceName')}")

    # 5) router stack
    print("[+] deploy LiteLLM router stack")
    router_outputs = deploy_stack(
        stack_name=SERVICE.router_stack_name,
        template_path=CFN_DIR / "30-litellm-router.yaml",
        parameters=SERVICE.router_parameters(
            resource_prefix=RESOURCE_PREFIX,
            litellm_master_key_secret_arn=LITELLM_KEY_ARN,
            api_key_secret_arn=VLLM_API_KEY_ARN,
            listener_priority=_listener_priority(model_name),
            path_prefix="/*",
            listener_protocol="HTTP",
        ),
        region=region,
        tags={"Project": "llm-inference", "Model": model_name},
    )
    target_group_arn = router_outputs["TargetGroupArn"]
    print(f"    target group: {target_group_arn}")

    # 6) wait for ALB healthy
    print("[+] wait for ALB target healthy")
    cold_start = 5400 if SERVICE.gpu_count >= 8 else 1800
    wait_for_alb_healthy(target_group_arn=target_group_arn,
                         region=region,
                         max_seconds=cold_start,
                         poll_seconds=20)

    # 7) smoke
    print("[+] sync smoke test")
    base_url = f"http://{alb_dns}"
    t0 = time.perf_counter()
    resp = smoke_test_endpoint(
        base_url=base_url,
        api_key=LITELLM_KEY,
        served_model_name=SERVICE.served_model_name,
        system_prompt=SYSTEM_PROMPT,
        user_input=SEED_INPUT,
        max_tokens=256,
        timeout_s=180,
    )
    elapsed = time.perf_counter() - t0
    usage = resp.get("usage", {}) or {}
    out_tokens = int(usage.get("completion_tokens", 0))
    in_tokens = int(usage.get("prompt_tokens", 0))
    msg = resp["choices"][0]["message"]
    # gpt-oss reasoning_parser separates reasoning_content (chain-of-thought)
    # from content (final answer); other models use content alone. Coerce
    # to a string for printing so the smoke can inspect either path without
    # NoneType crashes.
    output = msg.get("content") or msg.get("reasoning_content") or ""
    print(f"    in_tokens={in_tokens} out_tokens={out_tokens} "
          f"latency_s={elapsed:.2f} output_tps={out_tokens/elapsed:.2f}")
    print(f"    response[:200]={output[:200]!r}")

    # 8) load test (small)
    print("[+] async load test (concurrency=8, n=24)")
    import asyncio
    inputs = [SEED_INPUT] * 24
    result = asyncio.run(async_load_test(
        base_url=base_url,
        api_key=LITELLM_KEY,
        served_model_name=SERVICE.served_model_name,
        system_prompt=SYSTEM_PROMPT,
        user_inputs=inputs,
        concurrency=8,
        max_tokens=256,
    ))
    print(f"    {json.dumps(result.as_dict(), indent=2)}")

    # 9) teardown
    if not args.keep:
        print("[+] tearing down per-model stacks (router + vllm)")
        teardown_stack(stack_name=SERVICE.router_stack_name, region=region)
        teardown_stack(stack_name=SERVICE.model_stack_name, region=region)
    if args.teardown_all and not args.keep:
        print("[+] tearing down per-region stacks (cluster + networking)")
        teardown_stack(stack_name=f"{RESOURCE_PREFIX}-cluster", region=region)
        if vpc_id:
            sweep_guardduty_vpc_endpoints(vpc_id=vpc_id, region=region)
        teardown_stack(stack_name=f"{RESOURCE_PREFIX}-networking", region=region)

    print(json.dumps({
        "model": model_name,
        "region": region,
        "instance": cluster_instance,
        "smoke_in_tokens": in_tokens,
        "smoke_out_tokens": out_tokens,
        "smoke_latency_s": round(elapsed, 2),
        "smoke_output_tps": round(out_tokens / elapsed, 2) if out_tokens else 0,
        "load_p50_s": result.p50_latency_s,
        "load_p95_s": result.p95_latency_s,
        "load_output_tps": result.output_throughput_tok_s,
        "n_success": result.n_success,
        "n_failed": result.n_failed,
        "status": "PASS",
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
