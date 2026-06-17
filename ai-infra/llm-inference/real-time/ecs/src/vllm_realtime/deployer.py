"""CFN deployer + secret upsert helpers used by the real-time/ecs notebook."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_RESERVED_ENV_NAMES = frozenset({
    "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "VLLM_API_KEY", "HF_HOME",
})


CFN_DIR = Path(__file__).resolve().parents[2] / "cfn"


@dataclass
class ModelService:
    """Per-model CFN params for the 20-vllm-model + 30-litellm-router stacks."""

    model_name: str
    hf_model_id: str
    served_model_name: str
    tensor_parallel: int = 1
    data_parallel: int = 1
    gpu_count: int = 1
    max_model_len: int = 16384
    extra_serve_flags: str = ""
    extra_env_vars: dict[str, str] = field(default_factory=dict)
    """Plan-author-provided env vars rendered as a JSON object into the
    ``ExtraEnvVarsJson`` template parameter (e.g.
    ``{"VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8": "1"}`` for gpt-oss-20b on
    Blackwell). Names must match ``[A-Z_][A-Z0-9_]*`` and not collide with
    framework-reserved names. The container entrypoint exports them before
    ``vllm serve``."""
    transformers_override_package: str = ""
    """Optional pip package spec installed before ``vllm serve`` runs (e.g.
    ``transformers>=4.57`` for Qwen3-VL-30B-A3B which needs
    Qwen3VLMoeForConditionalGeneration). Empty = no override."""
    gated: bool = False
    task_cpu: int = 4096
    task_memory_mib: int = 16384
    # ECS HealthCheck after StartPeriod: total tolerance =
    # health_check_retries * health_check_interval_s seconds. The default
    # budget (10 * 60 = 600s = 10 min after StartPeriod, total 15 min)
    # covers Qwen3-8B (~17 GiB) and MedGemma-27B (~55 GiB) but is too tight
    # for Llama-4-Scout (218 GiB BF16 weights, 18-75 min HF download).
    # Bump health_check_interval_s to 300 for that model so the total
    # tolerance is ~55 min.
    health_check_interval_s: int = 60
    health_check_retries: int = 10

    def __post_init__(self) -> None:
        for name in self.extra_env_vars:
            if not _ENV_NAME_RE.match(name):
                raise ValueError(
                    f"extra_env_vars name {name!r} must match [A-Z_][A-Z0-9_]*."
                )
            if name in _RESERVED_ENV_NAMES:
                raise ValueError(
                    f"extra_env_vars name {name!r} is reserved and cannot be "
                    "set via plan."
                )

    @property
    def model_stack_name(self) -> str:
        return f"llm-rt-vllm-{self.model_name}"

    @property
    def router_stack_name(self) -> str:
        return f"llm-rt-litellm-{self.model_name}"

    def model_parameters(
        self,
        *,
        resource_prefix: str,
        vllm_image: str,
        hf_token_secret_arn: str,
        api_key_secret_arn: str,
    ) -> list[dict[str, str]]:
        params = {
            "ResourcePrefix": resource_prefix,
            "ModelName": self.model_name,
            "HfModelId": self.hf_model_id,
            "ServedModelName": self.served_model_name,
            "VllmImage": vllm_image,
            "TensorParallel": str(self.tensor_parallel),
            "DataParallel": str(self.data_parallel),
            "GpuCount": str(self.gpu_count),
            "MaxModelLen": str(self.max_model_len),
            "ExtraServeFlags": self.extra_serve_flags,
            "ExtraEnvVarsJson": json.dumps(
                self.extra_env_vars, sort_keys=True, separators=(",", ":")
            ),
            "TransformersOverridePackage": self.transformers_override_package,
            "VllmApiKeySecretArn": api_key_secret_arn,
            "TaskCpu": str(self.task_cpu),
            "TaskMemoryMiB": str(self.task_memory_mib),
            "HealthCheckInterval": str(self.health_check_interval_s),
            "HealthCheckRetries": str(self.health_check_retries),
        }
        if self.gated:
            params["HfTokenSecretArn"] = hf_token_secret_arn
        return [{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()]

    def router_parameters(
        self,
        *,
        resource_prefix: str,
        litellm_master_key_secret_arn: str,
        api_key_secret_arn: str,
        litellm_image: str = "ghcr.io/berriai/litellm:main-stable",
        listener_priority: int = 100,
        path_prefix: str = "/*",
        listener_protocol: str = "HTTPS",
    ) -> list[dict[str, str]]:
        if listener_protocol not in ("HTTP", "HTTPS"):
            raise ValueError(
                f"listener_protocol must be HTTP or HTTPS, got {listener_protocol!r}"
            )
        params = {
            "ResourcePrefix": resource_prefix,
            "ModelName": self.model_name,
            "ServedModelName": self.served_model_name,
            "LiteLLMImage": litellm_image,
            "LiteLLMMasterKeySecretArn": litellm_master_key_secret_arn,
            "VllmApiKeySecretArn": api_key_secret_arn,
            "AlbListenerPriority": str(listener_priority),
            "PathPrefix": path_prefix,
            "ListenerProtocol": listener_protocol,
        }
        return [{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()]


# ---------------------------------------------------------------------------
# CFN helpers
# ---------------------------------------------------------------------------
# States from which CFN's update_stack() is rejected. Some (DELETE_COMPLETE)
# free the stack-name slot and need no cleanup; the rest still occupy it and
# require an explicit delete_stack() before a fresh create_stack().
#
# UPDATE_ROLLBACK_FAILED occurs when an update fails AND the auto-rollback
# itself also fails — the only recovery options CFN offers are
# continue-update-rollback (which usually fails again for the same reason)
# or delete-stack. update_stack() is rejected, so deploy_stack must treat
# this like ROLLBACK_FAILED: delete the stuck stack before recreating.
_NOT_UPDATABLE_STATES = frozenset({
    "DELETE_COMPLETE",
    "ROLLBACK_COMPLETE",
    "ROLLBACK_FAILED",
    "CREATE_FAILED",
    "DELETE_FAILED",
    "UPDATE_ROLLBACK_FAILED",
})

# DELETE_COMPLETE is the one not-updatable state where the slot is free —
# create_stack() proceeds without an explicit delete-first.
_REQUIRES_DELETE_BEFORE_CREATE = _NOT_UPDATABLE_STATES - {"DELETE_COMPLETE"}


def _stack_in_unrecoverable_state(cfn, stack_name: str) -> bool:
    """True iff describing the stack returns a status that still occupies
    the stack-name slot but blocks update_stack(). The caller must delete
    the stack first, then create.
    """
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        if "does not exist" in str(exc):
            return False
        raise
    status = resp["Stacks"][0]["StackStatus"]
    return status in _REQUIRES_DELETE_BEFORE_CREATE


def _stack_exists(cfn, stack_name: str) -> bool:
    """True only if a stack with this name is in an updateable state.

    CFN keeps DELETE_COMPLETE stacks queryable for ~90 days, and a failed
    create leaves the stack in ROLLBACK_COMPLETE (or ROLLBACK_FAILED /
    CREATE_FAILED). Update_stack() rejects all of these — they must either
    be deleted first or treated as "doesn't exist" so deploy_stack picks
    create_stack(). Return True only for states from which an update is
    actually permitted.
    """
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        if "does not exist" in str(exc):
            return False
        raise
    status = resp["Stacks"][0]["StackStatus"]
    return status not in _NOT_UPDATABLE_STATES


def deploy_stack(
    *,
    stack_name: str,
    template_path: Path | str,
    parameters: list[dict[str, str]] | None = None,
    region: str = "us-west-2",
    tags: dict[str, str] | None = None,
    capabilities: tuple[str, ...] = ("CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"),
    poll_seconds: int = 15,
    wait_timeout_s: int = 3600,
) -> dict[str, Any]:
    """Create or update a CFN stack and wait for stabilization.

    Returns the stack's outputs as a dict.

    ``wait_timeout_s`` is the upper bound on the CFN waiter — must cover the
    *slowest* resource in the stack. For ``20-vllm-model.yaml`` the slow
    resource is ``AWS::ECS::Service``, which CFN holds in CREATE_IN_PROGRESS
    until one task is running and the deployment is stable. For
    Llama-4-Scout (218 GiB BF16 weights, 18-75 min from HuggingFace +
    ~5 min ASG provisioning + ~5 min image pull + ~3 min warmup), the worst
    case is ~90 min — so the notebook bumps this to 7200s for the 8-GPU
    plan. The 1-hour default suffices for every other stack (networking,
    cluster, smaller-model 20-vllm-model, 30-litellm-router).
    """
    cfn = boto3.client("cloudformation", region_name=region)
    template_body = Path(template_path).read_text()
    args: dict[str, Any] = {
        "StackName": stack_name,
        "TemplateBody": template_body,
        "Capabilities": list(capabilities),
    }
    if parameters:
        args["Parameters"] = parameters
    if tags:
        args["Tags"] = [{"Key": k, "Value": v} for k, v in tags.items()]

    if _stack_exists(cfn, stack_name):
        try:
            cfn.update_stack(**args)
            waiter = cfn.get_waiter("stack_update_complete")
        except ClientError as exc:
            if "No updates are to be performed" in str(exc):
                return _stack_outputs(cfn, stack_name)
            raise
    else:
        # _stack_exists() also returns False for ROLLBACK_COMPLETE /
        # CREATE_FAILED, which still occupy the stack name. CFN's create_stack
        # rejects those with AlreadyExistsException — delete the old stack
        # first so the new create succeeds.
        if _stack_in_unrecoverable_state(cfn, stack_name):
            cfn.delete_stack(StackName=stack_name)
            cfn.get_waiter("stack_delete_complete").wait(
                StackName=stack_name,
                WaiterConfig={"Delay": poll_seconds,
                              "MaxAttempts": max(1, wait_timeout_s // poll_seconds)},
            )
        cfn.create_stack(OnFailure="DELETE", **args)
        # Sleep briefly so CFN finishes registering the stack before the
        # waiter starts polling describe_stacks. Without this, the boto3
        # waiter may catch a `ValidationError: Stack [X] does not exist`
        # response from a still-propagating create_stack and mis-classify
        # it as a terminal failure.
        time.sleep(8)
        waiter = cfn.get_waiter("stack_create_complete")
    _wait_with_does_not_exist_retry(
        waiter=waiter,
        stack_name=stack_name,
        poll_seconds=poll_seconds,
        wait_timeout_s=wait_timeout_s,
    )
    return _stack_outputs(cfn, stack_name)


def _wait_with_does_not_exist_retry(*, waiter, stack_name: str,
                                    poll_seconds: int,
                                    wait_timeout_s: int,
                                    max_does_not_exist_retries: int = 6
                                    ) -> None:
    """Wrap waiter.wait() with a retry on the ValidationError race condition.

    boto3's StackCreateComplete waiter classifies any ValidationError as
    terminal, but `describe_stacks` returns ValidationError "Stack [X] does
    not exist" between create_stack returning and CFN registering the new
    stack — that's a transient race, not a real failure. Retry up to N
    times, sleeping between, before giving up.
    """
    from botocore.exceptions import WaiterError as _WE
    delay = poll_seconds
    attempts = max(1, wait_timeout_s // poll_seconds)
    cooldown = 5
    for _ in range(max_does_not_exist_retries + 1):
        try:
            waiter.wait(
                StackName=stack_name,
                WaiterConfig={"Delay": delay, "MaxAttempts": attempts},
            )
            return
        except _WE as exc:
            if not _waiter_failed_due_to_does_not_exist(exc):
                raise
            time.sleep(cooldown)
            cooldown = min(cooldown * 2, 30)
    raise RuntimeError(
        f"Stack {stack_name!r} still 'does not exist' after "
        f"{max_does_not_exist_retries} retries"
    )


def _waiter_failed_due_to_does_not_exist(exc) -> bool:
    """Return True iff the WaiterError was caused by `describe_stacks`
    returning ValidationError "Stack [X] does not exist".

    boto3's WaiterError reason only contains "Matched expected service
    error code: ValidationError" — the human-readable "does not exist"
    text lives in ``last_response`` (or the underlying ClientError body).
    """
    last = getattr(exc, "last_response", None)
    if isinstance(last, dict):
        message = (last.get("Error") or {}).get("Message") or ""
        if "does not exist" in message:
            return True
    # Fall through to a string check in case boto3's representation changes.
    return "does not exist" in str(exc)


def teardown_stack(*, stack_name: str, region: str = "us-west-2",
                   poll_seconds: int = 15,
                   wait_timeout_s: int = 3600) -> None:
    """Delete a CFN stack and wait until it is gone.

    Audit fix #19: when a LiteLLM-router stack hits ``DELETE_FAILED`` (the
    ECS Service has a 1800s deregistration_delay vs. CFN's ~3600s wait but
    transient state machinery sometimes mistimes), retry the delete with
    ``--retain-resources Service`` so CFN drops the rest of the stack and
    the orphan TargetGroup + ALB listener-rule clean up. Otherwise the next
    deployment for a different model collides on the listener-rule priority
    and returns 503.
    """
    cfn = boto3.client("cloudformation", region_name=region)
    if not _stack_exists(cfn, stack_name):
        return
    cfn.delete_stack(StackName=stack_name)
    waiter = cfn.get_waiter("stack_delete_complete")
    try:
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={"Delay": poll_seconds,
                          "MaxAttempts": max(1, wait_timeout_s // poll_seconds)},
        )
        return
    except Exception:  # noqa: BLE001 — boto3 WaiterError or ClientError
        pass
    # If we reach here the stack is in DELETE_FAILED. Identify which logical
    # ids failed to delete so we can retain just those — almost always the
    # ECS Service for LiteLLM-router stacks. Retain by logical id so a fresh
    # deploy is unaffected (the orphan ALB target group and listener rule
    # are NOT retained — CFN deletes them on the retry).
    try:
        resp = cfn.describe_stack_resources(StackName=stack_name)
    except ClientError:
        return
    retain = [
        r["LogicalResourceId"]
        for r in resp.get("StackResources", [])
        if r.get("ResourceStatus") == "DELETE_FAILED"
    ]
    if not retain:
        # Stack failed but no individual DELETE_FAILED resource — re-raise
        # by attempting a plain delete again so the caller surfaces the
        # underlying boto3 WaiterError instead of swallowing it silently.
        cfn.delete_stack(StackName=stack_name)
        cfn.get_waiter("stack_delete_complete").wait(
            StackName=stack_name,
            WaiterConfig={"Delay": poll_seconds,
                          "MaxAttempts": max(1, wait_timeout_s // poll_seconds)},
        )
        return
    cfn.delete_stack(StackName=stack_name, RetainResources=retain)
    cfn.get_waiter("stack_delete_complete").wait(
        StackName=stack_name,
        WaiterConfig={"Delay": poll_seconds,
                      "MaxAttempts": max(1, wait_timeout_s // poll_seconds)},
    )


def sweep_guardduty_vpc_endpoints(*, vpc_id: str,
                                  region: str = "us-west-2",
                                  ec2_client=None) -> list[str]:
    """Delete AWS-managed GuardDuty VPC interface endpoints in the given VPC.

    GuardDuty VPC monitoring auto-attaches an interface endpoint
    (``com.amazonaws.<region>.guardduty-data``) to every new VPC. The endpoint
    pins ENIs in the subnets and blocks ``00-networking`` deletion with
    ``DependencyViolation``. Call this before ``teardown_stack`` for the
    networking stack. Returns the list of endpoint IDs that were deleted.

    Also sweeps the GuardDuty-managed security group
    (``GuardDutyManagedSecurityGroup-<vpc-id>``). This group is auto-attached
    when GuardDuty Runtime Monitoring is enabled at the account level and
    likewise blocks VPC delete with a DependencyViolation. The deleted
    SG ids are appended to the returned list.
    """
    ec2 = ec2_client or boto3.client("ec2", region_name=region)
    service_name = f"com.amazonaws.{region}.guardduty-data"
    resp = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "service-name", "Values": [service_name]},
        ],
    )
    endpoint_ids = [e["VpcEndpointId"] for e in resp.get("VpcEndpoints", [])]
    if endpoint_ids:
        ec2.delete_vpc_endpoints(VpcEndpointIds=endpoint_ids)
    # GuardDuty managed SG (Runtime Monitoring at account level).
    sgs = ec2.describe_security_groups(Filters=[
        {"Name": "vpc-id", "Values": [vpc_id]},
        {"Name": "group-name",
         "Values": [f"GuardDutyManagedSecurityGroup-{vpc_id}"]},
    ]).get("SecurityGroups", [])
    deleted = list(endpoint_ids)
    for sg in sgs:
        sg_id = sg["GroupId"]
        try:
            ec2.delete_security_group(GroupId=sg_id)
            deleted.append(sg_id)
        except Exception:  # noqa: BLE001 — we want best-effort cleanup
            pass
    return deleted


def _stack_outputs(cfn, stack_name: str) -> dict[str, Any]:
    desc = cfn.describe_stacks(StackName=stack_name)
    out = {}
    for o in desc["Stacks"][0].get("Outputs", []) or []:
        out[o["OutputKey"]] = o["OutputValue"]
    return out


# ---------------------------------------------------------------------------
# Secrets helpers
# ---------------------------------------------------------------------------
def upsert_secret(*, name: str, value: str, region: str = "us-west-2",
                  description: str = "",
                  model_name: str | None = None,
                  rotate_if_exists: bool = True) -> str:
    """Create or update a Secrets Manager secret. Returns its ARN.

    Tag policy: every AWS resource carries Project + Model tags so cleanup
    automation can sweep by model. Pass ``model_name`` for per-model
    secrets (HF token, vLLM API key, LiteLLM master key) so the secret is
    swept along with its stack on cleanup.

    ``rotate_if_exists=True`` (default) overwrites an existing secret with
    the new value — appropriate for HF tokens or any value the caller
    *wants* to rotate. ``rotate_if_exists=False`` makes the operation
    idempotent: if the secret already exists, return its ARN without
    changing the stored value. This is essential for symmetric keys
    consumed by long-running services (vLLM API key, LiteLLM master key)
    — overwriting them while ECS tasks have them mounted would desync
    the upstream/downstream auth and force a service restart to recover.
    """
    sm = boto3.client("secretsmanager", region_name=region)
    tags = [{"Key": "Project", "Value": "llm-inference"}]
    if model_name:
        tags.append({"Key": "Model", "Value": model_name})
    try:
        resp = sm.create_secret(
            Name=name,
            SecretString=value,
            Description=description,
            Tags=tags,
        )
        return resp["ARN"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceExistsException":
            if rotate_if_exists:
                sm.put_secret_value(SecretId=name, SecretString=value)
            return sm.describe_secret(SecretId=name)["ARN"]
        raise


# ---------------------------------------------------------------------------
# ALB readiness
# ---------------------------------------------------------------------------
def wait_for_alb_healthy(*, target_group_arn: str, region: str = "us-west-2",
                         max_seconds: int = 1800,
                         poll_seconds: int = 15) -> bool:
    """Block until at least one target in the group is healthy."""
    elbv2 = boto3.client("elbv2", region_name=region)
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        resp = elbv2.describe_target_health(TargetGroupArn=target_group_arn)
        states = [t["TargetHealth"]["State"] for t in resp.get("TargetHealthDescriptions", [])]
        if any(s == "healthy" for s in states):
            return True
        time.sleep(poll_seconds)
    raise TimeoutError(f"No healthy targets in {target_group_arn} after {max_seconds}s")


__all__ = [
    "CFN_DIR",
    "ModelService",
    "deploy_stack",
    "teardown_stack",
    "sweep_guardduty_vpc_endpoints",
    "upsert_secret",
    "wait_for_alb_healthy",
]
