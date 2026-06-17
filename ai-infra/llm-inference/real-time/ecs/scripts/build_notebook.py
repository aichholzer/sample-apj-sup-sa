"""Generate real-time/ecs/notebooks/real-time-ecs.ipynb programmatically.

Usage::

    python real-time/ecs/scripts/build_notebook.py

Notebook content lives here in code so changes show up in git diffs and
the notebook itself stays trivially regenerable.
"""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

NB_PATH = (
    Path(__file__).resolve().parents[1] / "notebooks" / "real-time-ecs.ipynb"
)


def md(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


def cells() -> list[dict]:
    return [
        md(dedent("""\
            # Real-time inference: vLLM on ECS-on-EC2 + LiteLLM router on Fargate

            ```
            Internet -> ALB (HTTP/HTTPS) -> LiteLLM Fargate -> AWS Cloud Map -> vLLM ECS-on-EC2 GPU tasks
            ```

            This notebook walks through:

            1. Pick a model from `real-time/ecs/models/` (one of `qwen3_8b`,
               `medgemma_27b`, `llama_4_scout_17b`).
            2. Deploy `00-networking.yaml` (one-time per region).
            3. Deploy `10-cluster.yaml` (one-time per region).
            4. Upsert HF token + vLLM API key + LiteLLM master key in Secrets Manager.
            5. Deploy `20-vllm-model.yaml` for the chosen model.
            6. Deploy `30-litellm-router.yaml` for the chosen model.
            7. Wait for ALB target health.
            8. Curl smoke test.
            9. Async load test (200 requests at concurrency=16).
            10. Teardown stacks.

            **Cost note:** GPU instances are launched via spot first (4:1 spot:on-demand
            cluster strategy). The LiteLLM router auto-scales on
            `ALBRequestCountPerTarget`. The vLLM service auto-scales on
            `ECSServiceAverageCPUUtilization` (vLLM is registered in Cloud Map, not
            an ALB target group, so request-per-target isn't available there).
            Cold start is ~10 minutes (model pull + load).
            """)),
        md("## 0. Setup"),
        code(dedent("""\
            import json
            import os
            import secrets as _secrets
            import sys
            from pathlib import Path

            NB_DIR = Path.cwd()
            for parent in [NB_DIR, *NB_DIR.parents]:
                if (parent / "src" / "vllm_realtime").is_dir():
                    PROJECT_ROOT = parent
                    break
            else:
                raise RuntimeError("Cannot find real-time/ecs project root from CWD")
            sys.path.insert(0, str(PROJECT_ROOT / "src"))
            sys.path.insert(0, str(PROJECT_ROOT / "models"))

            import boto3
            sts = boto3.client("sts")
            print("AWS account:", sts.get_caller_identity()["Account"])
            print("Project root:", PROJECT_ROOT)
            """)),
        md("## 1. Pick model + region"),
        code(dedent("""\
            REGION = "us-west-2"
            RESOURCE_PREFIX = "llm-inference-rt"

            # Pick one of: qwen3_8b, mistral_small_3_2_24b, qwen3_30b_a3b,
            # gemma_4_31b, medgemma_27b, llama_4_scout_17b, gpt_oss_20b,
            # qwen3_coder_next, qwen3_vl_30b_a3b
            MODEL_PKG = "qwen3_8b"

            # ACM certificate ARN to terminate TLS on the ALB. The cert MUST
            # be in the same region as REGION above.
            #
            # When ALB_CERTIFICATE_ARN is non-empty, the prod networking
            # template ``00-networking.yaml`` is used (HTTPS:443 only — no
            # plaintext listener). When empty, the dev networking template
            # ``00-networking-dev.yaml`` is used (adds HTTP:80 fixed-404
            # listener for cert-less smoke testing).
            ALB_CERTIFICATE_ARN = ""
            NETWORKING_TEMPLATE = (
                "00-networking.yaml" if ALB_CERTIFICATE_ARN
                else "00-networking-dev.yaml"
            )
            LISTENER_PROTOCOL = "HTTPS" if ALB_CERTIFICATE_ARN else "HTTP"
            ALB_PORT = 443 if LISTENER_PROTOCOL == "HTTPS" else 80
            ALB_SCHEME = "https" if LISTENER_PROTOCOL == "HTTPS" else "http"

            from importlib import import_module
            mod = import_module(MODEL_PKG)
            SERVICE = mod.SERVICE
            SYSTEM_PROMPT = mod.SYSTEM_PROMPT
            SEED_INPUT = mod.SEED_INPUT
            print("Model:", SERVICE.model_name, "(", SERVICE.hf_model_id, ")")
            print("GPUs:", SERVICE.gpu_count, "TP=", SERVICE.tensor_parallel,
                  "DP=", SERVICE.data_parallel)
            print("Gated:", SERVICE.gated)
            """)),
        md("## 2. Deploy networking stack (one-time per region)"),
        code(dedent("""\
            from vllm_realtime import deploy_stack, CFN_DIR

            net_outputs = deploy_stack(
                stack_name=f"{RESOURCE_PREFIX}-networking",
                template_path=CFN_DIR / NETWORKING_TEMPLATE,
                parameters=[
                    {"ParameterKey": "ResourcePrefix", "ParameterValue": RESOURCE_PREFIX},
                    # Cert empty -> dev template is used and an HTTP:80
                    # fixed-404 listener is added; router stack attaches to
                    # HTTP. Cert non-empty -> prod template is used; only
                    # the HTTPS:443 listener exists and the router attaches
                    # to it. NETWORKING_TEMPLATE above selects which file.
                    {"ParameterKey": "AlbCertificateArn", "ParameterValue": ALB_CERTIFICATE_ARN},
                ],
                region=REGION,
                tags={"Project": "llm-inference"},
            )
            ALB_DNS = net_outputs["AlbDns"]
            print("ALB:", ALB_DNS, f"(listener: {LISTENER_PROTOCOL}:{ALB_PORT})")
            """)),
        md(dedent("""\
            ## 3. Deploy cluster stack (one-time per region per instance family)

            The cluster's ASG launches one specific GPU instance type. If you
            want to host two different models that need different instance
            families (e.g. `g7e.2xlarge` for Qwen3-8B and `p4d.24xlarge` for
            Llama-4-Scout), deploy the cluster stack twice with different
            `ResourcePrefix` values. The cell below picks an instance type
            sized for the SERVICE you selected in section 1.
            """)),
        code(dedent("""\
            # Pick a cluster InstanceType big enough for SERVICE.gpu_count.
            # Edit this map for your account's available capacity.
            _CLUSTER_INSTANCE_BY_GPU = {
                1: "g7e.2xlarge",
                4: "g6e.12xlarge",   # 4xL40S — Qwen3-Coder-Next, Qwen3-VL-30B-A3B
                8: "p4d.24xlarge",
            }
            CLUSTER_INSTANCE_TYPE = _CLUSTER_INSTANCE_BY_GPU.get(SERVICE.gpu_count, "g7e.2xlarge")
            # Root volume must hold the ECS-optimized AMI (~10 GiB) + the vLLM
            # image (~10 GiB) + the full HuggingFace cache for the model. Use
            # 400 GiB for the 8-GPU box (Llama-4-Scout is ~218 GiB BF16) and
            # 150 GiB for single-GPU boxes (Qwen3-8B is ~16 GiB, MedGemma-27B
            # is ~54 GiB).
            CLUSTER_ROOT_VOLUME_GIB = 400 if SERVICE.gpu_count >= 8 else 150
            print("Cluster instance type:", CLUSTER_INSTANCE_TYPE,
                  "root vol GiB:", CLUSTER_ROOT_VOLUME_GIB)

            cluster_outputs = deploy_stack(
                stack_name=f"{RESOURCE_PREFIX}-cluster",
                template_path=CFN_DIR / "10-cluster.yaml",
                parameters=[
                    {"ParameterKey": "ResourcePrefix",     "ParameterValue": RESOURCE_PREFIX},
                    {"ParameterKey": "InstanceType",       "ParameterValue": CLUSTER_INSTANCE_TYPE},
                    {"ParameterKey": "RootVolumeSizeGiB",  "ParameterValue": str(CLUSTER_ROOT_VOLUME_GIB)},
                ],
                region=REGION,
                tags={"Project": "llm-inference"},
            )
            print("Cluster:", cluster_outputs.get("ClusterName"))
            """)),
        md("## 4. Upsert secrets (HF token, vLLM API key, LiteLLM master key)"),
        code(dedent("""\
            from vllm_realtime import upsert_secret

            HF_TOKEN = os.environ.get("HF_TOKEN") or "PASTE_YOUR_HF_TOKEN"
            assert HF_TOKEN.startswith("hf_") or not SERVICE.gated, (
                "Gated model needs a real HF token (set HF_TOKEN env var or paste above)."
            )

            HF_TOKEN_ARN = upsert_secret(
                name=f"{RESOURCE_PREFIX}/hf-token",
                value=HF_TOKEN,
                region=REGION,
                description="HuggingFace token for gated model pulls",
            )
            VLLM_API_KEY = _secrets.token_urlsafe(32)
            VLLM_API_KEY_ARN = upsert_secret(
                name=f"{RESOURCE_PREFIX}/{SERVICE.model_name}/vllm-api-key",
                value=VLLM_API_KEY,
                region=REGION,
                description="vLLM Authorization bearer key (used by LiteLLM upstream)",
                model_name=SERVICE.model_name,
            )
            # LiteLLM enforces that master_key starts with 'sk-' at proxy
            # startup; generate one with that prefix or the proxy refuses to
            # boot with `master_key must start with 'sk-'`.
            LITELLM_KEY = "sk-" + _secrets.token_urlsafe(32)
            LITELLM_KEY_ARN = upsert_secret(
                name=f"{RESOURCE_PREFIX}/{SERVICE.model_name}/litellm-master-key",
                value=LITELLM_KEY,
                region=REGION,
                description="LiteLLM master key (clients send this in Authorization)",
                model_name=SERVICE.model_name,
            )
            print("Secrets upserted.")
            """)),
        md("## 5. Deploy vLLM model stack"),
        code(dedent("""\
            VLLM_IMAGE = "vllm/vllm-openai:v0.20.2"

            # CFN holds AWS::ECS::Service in CREATE_IN_PROGRESS until at least
            # one task is running and stable. For Llama-4-Scout (218 GiB BF16),
            # the worst-case path is ~90 min: ASG (5) + image pull (5) +
            # HuggingFace download at 50 MiB/s (~75) + warmup (3). Bump the
            # CFN waiter's timeout to 7200s on the 8-GPU plan so it doesn't
            # raise WaiterError while CFN is still creating the service.
            _MODEL_STACK_TIMEOUT_S = 7200 if SERVICE.gpu_count >= 8 else 3600

            model_outputs = deploy_stack(
                stack_name=SERVICE.model_stack_name,
                template_path=CFN_DIR / "20-vllm-model.yaml",
                parameters=SERVICE.model_parameters(
                    resource_prefix=RESOURCE_PREFIX,
                    vllm_image=VLLM_IMAGE,
                    hf_token_secret_arn=HF_TOKEN_ARN,
                    api_key_secret_arn=VLLM_API_KEY_ARN,
                ),
                region=REGION,
                tags={"Project": "llm-inference", "Model": SERVICE.model_name},
                wait_timeout_s=_MODEL_STACK_TIMEOUT_S,
            )
            print("vLLM service Cloud Map DNS:", model_outputs.get("CloudMapServiceName"))
            """)),
        md(dedent("""\
            ## 6. Deploy LiteLLM router stack

            ALB ListenerRule priorities must be unique per listener, and
            `path-pattern` matches must not overlap. The defaults below
            (`priority=100`, `path_prefix="/*"`) are fine for a single
            model. If you deploy a second model on the same ALB, give it a
            different priority AND a non-overlapping path (e.g.
            `/qwen3-8b/*` for one model and `/medgemma-27b/*` for another),
            or place each behind its own ALB.
            """)),
        code(dedent("""\
            # Derive a deterministic priority from the model name so two
            # model stacks on the same ALB don't collide. Range: 100-999.
            # md5 (not hash()) so the value is stable across Python processes
            # — Python's built-in hash() is randomized per-process via
            # PYTHONHASHSEED. Pass usedforsecurity=False because this is a
            # non-security identifier-derivation use of MD5.
            import hashlib as _hashlib
            LISTENER_PRIORITY = 100 + (
                int(_hashlib.md5(SERVICE.model_name.encode(), usedforsecurity=False).hexdigest(), 16) % 900
            )

            router_outputs = deploy_stack(
                stack_name=SERVICE.router_stack_name,
                template_path=CFN_DIR / "30-litellm-router.yaml",
                parameters=SERVICE.router_parameters(
                    resource_prefix=RESOURCE_PREFIX,
                    litellm_master_key_secret_arn=LITELLM_KEY_ARN,
                    api_key_secret_arn=VLLM_API_KEY_ARN,
                    listener_priority=LISTENER_PRIORITY,
                    path_prefix="/*",
                    # Must match the listener the networking stack actually
                    # created (HTTPS only when ALB_CERTIFICATE_ARN was set).
                    # Mismatch -> deploy fails because the listener export
                    # the router tries to import doesn't exist.
                    listener_protocol=LISTENER_PROTOCOL,
                ),
                region=REGION,
                tags={"Project": "llm-inference", "Model": SERVICE.model_name},
            )
            TARGET_GROUP_ARN = router_outputs["TargetGroupArn"]
            print("Target group:", TARGET_GROUP_ARN)
            """)),
        md("## 7. Wait for ALB target healthy"),
        code(dedent("""\
            from vllm_realtime import wait_for_alb_healthy
            # Cold start: ECS service first pulls vllm image (large), then vLLM
            # downloads weights from HF and warms CUDA graphs. The grace
            # budget must cover the largest model in your lineup. Llama-4-Scout
            # is ~218 GiB BF16 — at 50-200 MiB/s from HF that is 18-75 min for
            # weights alone, plus image pull + warmup + ASG provisioning. Use
            # 90 min for the 8-GPU box; 30 min is plenty for single-GPU.
            _COLD_START_BUDGET_S = 5400 if SERVICE.gpu_count >= 8 else 1800
            wait_for_alb_healthy(target_group_arn=TARGET_GROUP_ARN,
                                 region=REGION,
                                 max_seconds=_COLD_START_BUDGET_S,
                                 poll_seconds=20)
            print("Target healthy.")
            """)),
        md("## 8. Curl smoke test (single chat completion)"),
        code(dedent("""\
            from vllm_realtime import smoke_test_endpoint

            resp = smoke_test_endpoint(
                base_url=f"{ALB_SCHEME}://{ALB_DNS}",
                api_key=LITELLM_KEY,
                served_model_name=SERVICE.served_model_name,
                system_prompt=SYSTEM_PROMPT,
                user_input=SEED_INPUT,
                max_tokens=256,
            )
            print(json.dumps(resp.get("usage", {}), indent=2))
            print()
            print("--- response ---")
            print(resp["choices"][0]["message"]["content"][:1200])
            """)),
        md("## 9. Async load test (200 requests at concurrency=16)"),
        code(dedent("""\
            from vllm_realtime import async_load_test
            import asyncio

            inputs = [SEED_INPUT] * 200
            result = await async_load_test(
                base_url=f"{ALB_SCHEME}://{ALB_DNS}",
                api_key=LITELLM_KEY,
                served_model_name=SERVICE.served_model_name,
                system_prompt=SYSTEM_PROMPT,
                user_inputs=inputs,
                concurrency=16,
                max_tokens=256,
            )
            print(json.dumps(result.as_dict(), indent=2))
            """)),
        md("## 10. Push concurrency higher to verify autoscaling kicks in"),
        code(dedent("""\
            burst = [SEED_INPUT] * 600
            burst_result = await async_load_test(
                base_url=f"{ALB_SCHEME}://{ALB_DNS}",
                api_key=LITELLM_KEY,
                served_model_name=SERVICE.served_model_name,
                system_prompt=SYSTEM_PROMPT,
                user_inputs=burst,
                concurrency=64,
                max_tokens=256,
            )
            print(json.dumps(burst_result.as_dict(), indent=2))
            # Inspect service desired count to confirm scale-out:
            ecs = boto3.client("ecs", region_name=REGION)
            svc = ecs.describe_services(
                cluster=cluster_outputs["ClusterName"],
                services=[f"{RESOURCE_PREFIX}-vllm-{SERVICE.model_name}"],
            )["services"][0]
            print("desiredCount:", svc["desiredCount"], "runningCount:", svc["runningCount"])
            """)),
        md("## 11. Teardown (commented — uncomment to actually delete)"),
        code(dedent("""\
            from vllm_realtime import teardown_stack
            from vllm_realtime.deployer import sweep_guardduty_vpc_endpoints

            # # Per-model stacks first:
            # teardown_stack(stack_name=SERVICE.router_stack_name, region=REGION)
            # teardown_stack(stack_name=SERVICE.model_stack_name, region=REGION)
            #
            # # Per-region stacks (only when no model stacks remain).
            # # NOTE: if GuardDuty VPC monitoring is enabled in this account,
            # # sweep its auto-attached interface endpoint BEFORE deleting the
            # # networking stack — otherwise CFN fails with DependencyViolation
            # # because the AWS-managed ENIs pin the subnets.
            # teardown_stack(stack_name=f"{RESOURCE_PREFIX}-cluster", region=REGION)
            # vpc_id = net_outputs["VpcId"]
            # sweep_guardduty_vpc_endpoints(vpc_id=vpc_id, region=REGION)
            # teardown_stack(stack_name=f"{RESOURCE_PREFIX}-networking", region=REGION)
            """)),
    ]


def build() -> dict:
    return {
        "cells": cells(),
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    nb = build()
    NB_PATH.parent.mkdir(parents=True, exist_ok=True)
    NB_PATH.write_text(json.dumps(nb, indent=1, ensure_ascii=False))
    print(f"Wrote notebook: {NB_PATH} ({NB_PATH.stat().st_size / 1024:.1f} KiB, {len(nb['cells'])} cells)")


if __name__ == "__main__":
    main()
