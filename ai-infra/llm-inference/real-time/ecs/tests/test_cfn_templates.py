"""Static tests for the four CFN templates in real-time/ecs/cfn/.

These run without AWS credentials. They check the templates parse as YAML,
declare the parameters the deployer expects, and reference resource types
the design calls for.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CFN_DIR = ROOT / "cfn"

sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# CFN-aware YAML loader (treats !Ref / !Sub / !GetAtt etc. as opaque scalars)
# ---------------------------------------------------------------------------
class _CfnLoader(yaml.SafeLoader):
    pass


def _construct_cfn_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_CfnLoader.add_multi_constructor("!", _construct_cfn_tag)


def _load(name: str) -> dict:
    # _CfnLoader is a yaml.SafeLoader subclass with extra constructors for
    # CFN intrinsic-function tags (!Ref, !Sub, etc). Bandit B506 flags any
    # yaml.load() but the loader here is a SafeLoader subclass — no
    # arbitrary object construction is possible.
    with open(CFN_DIR / name) as fp:
        return yaml.load(fp, Loader=_CfnLoader)  # nosec B506


def _all_resource_types(doc: dict) -> set[str]:
    types: set[str] = set()
    for r in (doc.get("Resources") or {}).values():
        types.add(r["Type"])
    return types


# ---------------------------------------------------------------------------
# 00-networking
# ---------------------------------------------------------------------------
def test_networking_parses():
    doc = _load("00-networking.yaml")
    assert "Resources" in doc
    types = _all_resource_types(doc)
    assert "AWS::EC2::VPC" in types
    assert "AWS::EC2::InternetGateway" in types
    assert "AWS::EC2::NatGateway" in types
    assert "AWS::ElasticLoadBalancingV2::LoadBalancer" in types
    # 3 AZ public + 3 AZ private subnets
    subnet_count = sum(1 for r in doc["Resources"].values() if r["Type"] == "AWS::EC2::Subnet")
    assert subnet_count == 6


def test_networking_creates_cloudmap_namespace():
    doc = _load("00-networking.yaml")
    types = _all_resource_types(doc)
    assert "AWS::ServiceDiscovery::PrivateDnsNamespace" in types


def test_networking_exports_alb_arn():
    """LiteLLM router stack imports ${ResourcePrefix}-alb-arn — ensure it exists."""
    doc = _load("00-networking.yaml")
    outputs = doc.get("Outputs") or {}
    export_names = []
    for o in outputs.values():
        export = o.get("Export") or {}
        if "Name" in export:
            export_names.append(str(export["Name"]))
    blob = " ".join(export_names)
    assert "alb-arn" in blob
    assert "listener" in blob.lower()


def test_alb_idle_timeout_covers_litellm_upstream_timeout():
    """Audit-shape regression #15 + #18: ALB idle_timeout must be >=
    LiteLLM's *retry-amplified* total wall-clock per request, i.e.
    ``timeout × (1 + num_retries)``.

    The LiteLLM router config (30-litellm-router.yaml) sets
    ``router_settings.timeout: 600`` (the per-attempt upstream wait) and
    ``num_retries: 2`` (so the router will try a third time if the first
    two attempts time out). For non-streaming requests
    (``/v1/chat/completions`` with ``stream=False``), no bytes flow back
    to the client until vLLM returns the full completion — the ALB sees
    the same connection as idle the entire time. Worst case: attempt 1
    hits the 600s ceiling and is retried, attempt 2 hits 600s and is
    retried, attempt 3 finally completes on the long tail. The connection
    has been held open for up to 1800s with no traffic.

    Bug #15 fixed the per-attempt case (idle_timeout >= 600). Bug #18
    fixes the retry-amplified case (idle_timeout >= 600 × 3 = 1800).
    Same nested-timeout audit shape: every enclosing wait must cover the
    *largest unit of work* it wraps, not just the per-attempt slice.
    """
    net = _load("00-networking.yaml")
    alb = next(
        v for v in net["Resources"].values()
        if v["Type"] == "AWS::ElasticLoadBalancingV2::LoadBalancer"
    )
    attrs = alb["Properties"]["LoadBalancerAttributes"]
    idle_attr = next(a for a in attrs
                     if a["Key"] == "idle_timeout.timeout_seconds")
    alb_idle_s = int(idle_attr["Value"])

    # Parse the LiteLLM upstream timeout AND num_retries from the router
    # CFN's config.yaml.
    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    m_timeout = re.search(r"^\s*timeout:\s*(\d+)\s*$", raw, re.MULTILINE)
    assert m_timeout, "30-litellm-router.yaml must set router_settings.timeout"
    litellm_timeout_s = int(m_timeout.group(1))
    m_retries = re.search(r"^\s*num_retries:\s*(\d+)\s*$", raw, re.MULTILINE)
    assert m_retries, "30-litellm-router.yaml must set router_settings.num_retries"
    litellm_num_retries = int(m_retries.group(1))

    # Per-attempt floor (bug #15): idle_timeout must cover one attempt.
    assert alb_idle_s >= litellm_timeout_s, (
        f"ALB idle_timeout={alb_idle_s}s must be >= LiteLLM upstream "
        f"timeout={litellm_timeout_s}s — otherwise the ALB closes a "
        "non-streaming connection LiteLLM is still happy to honor, "
        "surfacing as a spurious 504 on the long-tail path (bug #15)"
    )

    # Retry-amplified floor (bug #18): idle_timeout must cover
    # `timeout × (1 + num_retries)` since LiteLLM may consume the full
    # per-attempt budget on each retry without producing any bytes.
    retry_total_s = litellm_timeout_s * (1 + litellm_num_retries)
    assert alb_idle_s >= retry_total_s, (
        f"ALB idle_timeout={alb_idle_s}s must be >= LiteLLM's retry-"
        f"amplified total = timeout({litellm_timeout_s}) × (1 + "
        f"num_retries({litellm_num_retries})) = {retry_total_s}s. "
        "Otherwise the ALB closes a non-streaming connection while "
        "LiteLLM is still on retry attempt 2 or 3, surfacing as a "
        "spurious 504 on the worst-tail path (bug #18)."
    )


# ---------------------------------------------------------------------------
# 10-cluster
# ---------------------------------------------------------------------------
def test_cluster_has_two_capacity_providers_and_asgs():
    doc = _load("10-cluster.yaml")
    types = _all_resource_types(doc)
    assert "AWS::ECS::Cluster" in types
    asg_count = sum(1 for r in doc["Resources"].values()
                    if r["Type"] == "AWS::AutoScaling::AutoScalingGroup")
    cp_count = sum(1 for r in doc["Resources"].values()
                   if r["Type"] == "AWS::ECS::CapacityProvider")
    assert asg_count >= 2, "expected at least spot + on-demand ASG"
    assert cp_count >= 2, "expected at least spot + on-demand capacity provider"


def test_cluster_root_volume_size_is_parameter_with_safe_default():
    """The launch template's root volume must be sized to hold the model's
    HF weight cache. Llama-4-Scout-17B-16E is ~218 GiB BF16, so a 200 GiB
    default would have run out of space mid-pull. Make this a CFN parameter
    so the notebook can size up to 400 GiB for 8-GPU instances."""
    doc = _load("10-cluster.yaml")
    params = doc.get("Parameters") or {}
    assert "RootVolumeSizeGiB" in params, "expected RootVolumeSizeGiB parameter"
    default = params["RootVolumeSizeGiB"].get("Default")
    assert int(default) >= 300, (
        f"default root volume size must accommodate Llama-4-Scout BF16 "
        f"(~218 GiB), got {default}"
    )
    raw = (CFN_DIR / "10-cluster.yaml").read_text()
    # Make sure the BlockDeviceMappings actually references the parameter
    # rather than holding a hardcoded 200.
    assert "VolumeSize: !Ref RootVolumeSizeGiB" in raw or \
           "VolumeSize: !Ref 'RootVolumeSizeGiB'" in raw or \
           "!Ref RootVolumeSizeGiB" in raw


def test_cluster_managed_scaling_and_termination_protection_enabled():
    doc = _load("10-cluster.yaml")
    found_managed = False
    found_term_protection = False
    for r in doc["Resources"].values():
        if r["Type"] != "AWS::ECS::CapacityProvider":
            continue
        ap = r["Properties"].get("AutoScalingGroupProvider", {})
        ms = ap.get("ManagedScaling", {})
        if str(ms.get("Status", "")).upper() == "ENABLED":
            found_managed = True
        if str(ap.get("ManagedTerminationProtection", "")).upper() == "ENABLED":
            found_term_protection = True
    assert found_managed
    assert found_term_protection


# ---------------------------------------------------------------------------
# 20-vllm-model
# ---------------------------------------------------------------------------
def test_vllm_model_uses_awsvpc_and_gpu():
    doc = _load("20-vllm-model.yaml")
    task_defs = [r for r in doc["Resources"].values() if r["Type"] == "AWS::ECS::TaskDefinition"]
    assert task_defs, "expected at least one ECS::TaskDefinition"
    td = task_defs[0]["Properties"]
    assert td.get("NetworkMode") == "awsvpc"


def test_vllm_model_health_check_and_shared_memory():
    """vLLM container needs /health check + 16 GiB shared memory per the design doc."""
    doc = _load("20-vllm-model.yaml")
    raw = (CFN_DIR / "20-vllm-model.yaml").read_text()
    assert "/health" in raw
    assert "SharedMemorySize" in raw
    # 16 GiB = 16384 MiB
    assert "16384" in raw


def test_vllm_model_health_check_interval_and_retries_are_parameters():
    """The HealthCheck Interval + Retries on the vLLM container must be CFN
    parameters so per-model SERVICE configs can stretch the grace period.

    Llama-4-Scout's 218 GiB BF16 weights take 18-75 min to download from
    HuggingFace. The default grace (StartPeriod 300 + 10 * 60s Interval =
    15 min) would kill the task before vLLM finishes loading. The notebook
    can only override CFN parameters, so these MUST be parameterized — a
    hardcoded `Interval: 60` would silently break the largest model in the
    lineup.
    """
    doc = _load("20-vllm-model.yaml")
    params = doc.get("Parameters") or {}
    assert "HealthCheckInterval" in params, (
        "vLLM model template must declare HealthCheckInterval as a CFN "
        "parameter so per-model SERVICEs can stretch the grace period for "
        "large weight downloads (Llama-4-Scout = 218 GiB)."
    )
    assert "HealthCheckRetries" in params, (
        "vLLM model template must declare HealthCheckRetries as a CFN "
        "parameter."
    )

    raw = (CFN_DIR / "20-vllm-model.yaml").read_text()
    # The HealthCheck block must reference the parameters (not hardcode).
    assert "Interval: !Ref HealthCheckInterval" in raw, (
        "HealthCheck.Interval must reference the HealthCheckInterval "
        "parameter, not a hardcoded number."
    )
    assert "Retries: !Ref HealthCheckRetries" in raw, (
        "HealthCheck.Retries must reference the HealthCheckRetries "
        "parameter."
    )


def test_vllm_model_overrides_image_entrypoint():
    """The vllm/vllm-openai image ships an ENTRYPOINT (e.g. ["vllm", "serve"]
    or ["python3", "-m", "vllm.entrypoints.openai.api_server"]). ECS's
    Command is Docker's CMD (appended to ENTRYPOINT), NOT a shell — so a
    bare Command starting with /bin/bash would land as positional args to
    the entrypoint and the container would crash trying to interpret
    /bin/bash as a model id or subcommand.

    Fix: override EntryPoint to ['/bin/bash', '-lc'] so the wrapper script
    runs as a shell command. Wrapper script must `exec vllm serve ...` so
    SIGTERM still reaches the python process during scale-in.
    """
    raw = (CFN_DIR / "20-vllm-model.yaml").read_text()
    assert "EntryPoint:" in raw, (
        "vllm container must override EntryPoint; otherwise the image's "
        "default ENTRYPOINT swallows the Command as args"
    )
    assert "/bin/bash" in raw and "-lc" in raw
    # Wrapper must `exec vllm serve ...` so signals propagate.
    assert "exec vllm serve" in raw, (
        "wrapper must `exec vllm serve` so SIGTERM reaches the python "
        "process (otherwise ECS deregisters slowly on stop)"
    )


def test_vllm_model_default_image_supports_gemma4():
    """Gemma 4 (released Apr 2026) requires vLLM >= 0.11.0; v0.10.2 fails
    at startup with a transformers ValidationError on the gemma4 model type."""
    doc = _load("20-vllm-model.yaml")
    default = (doc.get("Parameters") or {}).get("VllmImage", {}).get("Default", "")
    assert default.startswith("vllm/vllm-openai:v0."), default
    tag = default.split(":")[1]
    major_minor = tag.lstrip("v").split(".")
    version_tuple = (int(major_minor[0]), int(major_minor[1]))
    assert version_tuple >= (0, 11), (
        f"VllmImage default {default} predates gemma4 support; need >=v0.11.0"
    )


def test_vllm_model_required_parameters():
    doc = _load("20-vllm-model.yaml")
    params = doc.get("Parameters") or {}
    for required in [
        "ResourcePrefix", "ModelName", "HfModelId", "ServedModelName",
        "VllmImage", "TensorParallel", "MaxModelLen",
        "VllmApiKeySecretArn",
    ]:
        assert required in params, f"missing parameter {required}"


def test_vllm_model_registers_in_cloudmap():
    doc = _load("20-vllm-model.yaml")
    types = _all_resource_types(doc)
    assert "AWS::ServiceDiscovery::Service" in types


def test_vllm_model_has_autoscaling_policy_with_asymmetric_cooldowns():
    """vLLM service has a ScalableTarget *and* a ScalingPolicy attached
    (a ScalableTarget alone would be inert)."""
    doc = _load("20-vllm-model.yaml")
    types = _all_resource_types(doc)
    assert "AWS::ApplicationAutoScaling::ScalableTarget" in types
    assert "AWS::ApplicationAutoScaling::ScalingPolicy" in types
    raw = (CFN_DIR / "20-vllm-model.yaml").read_text()
    # Asymmetric cooldowns: scale-out fast, scale-in slow.
    assert "ScaleOutCooldown: 60" in raw
    assert "ScaleInCooldown: 600" in raw


def test_vllm_service_uses_capacity_provider_strategy_not_launch_type():
    """The vLLM service must reference the cluster capacity providers, not
    LaunchType:EC2. The cluster ASGs ship with DesiredCapacity=0 and rely on
    ECS managed scaling to scale up; managed scaling only fires for services
    that use a CapacityProviderStrategy. With LaunchType:EC2, vLLM tasks would
    queue PENDING forever and no GPU instance would ever launch."""
    doc = _load("20-vllm-model.yaml")
    services = [r for r in doc["Resources"].values() if r["Type"] == "AWS::ECS::Service"]
    assert services, "expected at least one ECS::Service"
    svc = services[0]["Properties"]
    assert "LaunchType" not in svc, "must not pin LaunchType:EC2; use capacity providers"
    cps = svc.get("CapacityProviderStrategy") or []
    assert cps, "expected CapacityProviderStrategy"
    raw = (CFN_DIR / "20-vllm-model.yaml").read_text()
    # Reference both cluster-stack exports.
    assert "cp-spot" in raw
    assert "cp-od" in raw


# ---------------------------------------------------------------------------
# 30-litellm-router
# ---------------------------------------------------------------------------
def test_litellm_router_required_parameters():
    doc = _load("30-litellm-router.yaml")
    params = doc.get("Parameters") or {}
    for required in [
        "ResourcePrefix", "ModelName", "ServedModelName",
        "LiteLLMImage", "LiteLLMMasterKeySecretArn", "VllmApiKeySecretArn",
    ]:
        assert required in params, f"missing parameter {required}"


def test_litellm_router_uses_fargate():
    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    assert "FARGATE" in raw, "LiteLLM should run on Fargate per the design"


def test_litellm_router_targettracking_request_count():
    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    assert "ALBRequestCountPerTarget" in raw
    assert "TargetTrackingScaling" in raw


def test_litellm_router_listener_rule_priority():
    doc = _load("30-litellm-router.yaml")
    types = _all_resource_types(doc)
    assert "AWS::ElasticLoadBalancingV2::ListenerRule" in types
    assert "AWS::ElasticLoadBalancingV2::TargetGroup" in types


def test_router_opens_ingress_to_vllm_gpu_sg():
    """The router stack must open SG ingress on the vLLM GPU SG so LiteLLM
    can actually reach vLLM:8000 via Cloud Map. Originally the GPU SG only
    permitted ingress from the ALB SG, which made the LiteLLM->vLLM hop
    blocked at the network layer."""
    doc = _load("30-litellm-router.yaml")
    types = _all_resource_types(doc)
    assert "AWS::EC2::SecurityGroupIngress" in types
    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    assert "gpu-task-sg" in raw
    assert "FromPort: 8000" in raw


def test_cluster_gpu_sg_has_no_baseline_ingress():
    """Counterpart to test_router_opens_ingress_to_vllm_gpu_sg: the cluster
    stack must not declare baseline ingress on the GPU SG (would either be
    wrong or duplicate the router-stack rule)."""
    doc = _load("10-cluster.yaml")
    for r in doc["Resources"].values():
        if r["Type"] != "AWS::EC2::SecurityGroup":
            continue
        if "gpu" in str(r["Properties"].get("GroupDescription", "")).lower():
            assert "SecurityGroupIngress" not in r["Properties"]


def test_litellm_router_default_path_prefix_is_wildcard():
    """A bare '/' is a literal-only ALB pattern and would only match the
    root path, breaking OpenAI-style endpoints. Default must be '/*'."""
    doc = _load("30-litellm-router.yaml")
    params = doc.get("Parameters") or {}
    assert params.get("PathPrefix", {}).get("Default") == "/*"


def test_litellm_router_overrides_image_entrypoint():
    """The ghcr.io/berriai/litellm image's ENTRYPOINT exec's `litellm` directly
    (`exec litellm "$@"` in docker/prod_entrypoint.sh). A bare `Command` would
    therefore land as positional args to the litellm CLI — our config-writing
    one-liner would never run, and litellm would try to parse `/bin/bash` as
    a subcommand and exit 2.

    Fix: override the container's EntryPoint to ['/bin/bash', '-lc'] so the
    config-writing script runs first; that script ends with `exec litellm
    --config /app/config.yaml ...`."""
    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    assert "EntryPoint:" in raw, (
        "litellm container must override EntryPoint; otherwise the image "
        "default exec's `litellm` and our shell command never runs"
    )
    assert "/bin/bash" in raw and "-lc" in raw
    # The wrapper script must exec litellm so signals propagate cleanly.
    assert "exec litellm" in raw, (
        "wrapper script must `exec litellm` so SIGTERM reaches the python "
        "process (otherwise ECS deregisters slowly on stop)"
    )


def test_litellm_router_listener_protocol_parameter_with_safe_default():
    """The router stack must support attaching its ListenerRule to either
    the HTTP listener (port 80) or the HTTPS listener (port 443) created by
    the networking stack. The default is HTTPS so prod-shaped deploys
    against the production networking template (which only ships an
    HTTPS listener) work without any parameter overrides; pass HTTP
    explicitly when targeting the cert-less dev networking template."""
    doc = _load("30-litellm-router.yaml")
    params = doc.get("Parameters") or {}
    assert "ListenerProtocol" in params, "expected ListenerProtocol parameter"
    p = params["ListenerProtocol"]
    assert p.get("Default") == "HTTPS", (
        "default must be HTTPS so prod-shaped deploys work without overrides"
    )
    allowed = p.get("AllowedValues") or []
    assert set(allowed) == {"HTTP", "HTTPS"}, "AllowedValues must be HTTP or HTTPS"


def test_litellm_router_listener_rule_attaches_to_chosen_protocol():
    """The ListenerRule must reference whichever listener export matches
    the ListenerProtocol parameter. The original template hard-coded the
    HTTP listener (port 80), so a deploy with an ACM cert + HTTPS:443
    listener would silently fall through to that listener's default 404."""
    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    # Both listener exports must be referenced via Fn::ImportValue so the
    # !If chooses between them at deploy time.
    assert "alb-http-listener" in raw
    assert "alb-https-listener" in raw
    # The selector must be conditional on the ListenerProtocol parameter.
    assert "UseHttps" in raw, "expected an !If on UseHttps for the listener arn"


def test_litellm_router_target_group_does_not_pin_name():
    """ELB caps TargetGroup ``Name`` at 32 chars. The original template
    pinned ``Name: ${ResourcePrefix}-llm-${ModelName}-tg``, which overflows
    the cap for two of the three real-time-designated models —
    ``llm-inference-rt-llm-medgemma-27b-tg`` (36 chars) and
    ``llm-inference-rt-llm-llama-4-scout-tg`` (37 chars) — so deploys for
    both would have failed with ``Length of property Name exceeds the
    maximum permitted (32)``. Drop the explicit name and let CFN
    auto-generate; consumers reference the TG by ARN.
    """
    doc = _load("30-litellm-router.yaml")
    tg = next(
        r for r in doc["Resources"].values()
        if r["Type"] == "AWS::ElasticLoadBalancingV2::TargetGroup"
    )
    props = tg.get("Properties", {})
    assert "Name" not in props, (
        "TargetGroup must not pin a Name template — overflows ELB's 32-char "
        "cap on long ResourcePrefix+ModelName combos"
    )

    # Sanity: simulate the legacy template against the 3 designated models
    # so we'd have caught it at scaffold time. Picks up regressions if the
    # name template is ever reintroduced.
    legacy_template = "{prefix}-llm-{model}-tg"
    for prefix, model_name in [
        ("llm-inference-rt", "qwen3-8b"),
        ("llm-inference-rt", "medgemma-27b"),
        ("llm-inference-rt", "llama-4-scout"),
    ]:
        rendered = legacy_template.format(prefix=prefix, model=model_name)
        if model_name in ("medgemma-27b", "llama-4-scout"):
            assert len(rendered) > 32, (
                f"sanity: legacy name {rendered!r} ({len(rendered)} chars) "
                "should overflow ELB's 32-char cap — this assertion ensures "
                "the legacy template would have failed in CFN"
            )


def test_litellm_router_target_group_deregistration_delay_covers_upstream_timeout():
    """Audit-shape regression #16 + #18: TargetGroup ``deregistration_delay``
    must be >= LiteLLM's *retry-amplified* total per-request budget, i.e.
    ``timeout × (1 + num_retries)``.

    During a Fargate scale-in or rolling deploy, the ALB waits
    ``deregistration_delay.timeout_seconds`` for in-flight requests on the
    deregistering target before forcefully closing the connection. For
    non-streaming requests on the long tail, an in-flight request can
    sit in LiteLLM's upstream wait — including retries — for up to
    ``timeout × (1 + num_retries)``. With ``timeout: 600`` and
    ``num_retries: 2``, that's 1800s.

    Bug #16 fixed the per-attempt case (drain >= 600). Bug #18 fixes the
    retry-amplified case (drain >= 600 × 3 = 1800). Same audit shape:
    the *outer* wait (ALB drain) must cover the largest unit of work
    the *inner* layer (LiteLLM router) can hold the connection for.
    """
    doc = _load("30-litellm-router.yaml")
    tg = next(
        r for r in doc["Resources"].values()
        if r["Type"] == "AWS::ElasticLoadBalancingV2::TargetGroup"
    )
    attrs = tg["Properties"].get("TargetGroupAttributes") or []
    drain_attr = next(
        (a for a in attrs
         if a["Key"] == "deregistration_delay.timeout_seconds"),
        None,
    )
    assert drain_attr is not None, (
        "TargetGroup must set deregistration_delay.timeout_seconds — "
        "default of 300s is shorter than LiteLLM's 600s upstream timeout"
    )
    drain_s = int(drain_attr["Value"])

    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    m_timeout = re.search(r"^\s*timeout:\s*(\d+)\s*$", raw, re.MULTILINE)
    assert m_timeout, "30-litellm-router.yaml must set router_settings.timeout"
    litellm_timeout_s = int(m_timeout.group(1))
    m_retries = re.search(r"^\s*num_retries:\s*(\d+)\s*$", raw, re.MULTILINE)
    assert m_retries, "30-litellm-router.yaml must set router_settings.num_retries"
    litellm_num_retries = int(m_retries.group(1))

    # Per-attempt floor (bug #16): drain must cover one attempt.
    assert drain_s >= litellm_timeout_s, (
        f"TargetGroup deregistration_delay={drain_s}s must be >= LiteLLM "
        f"upstream timeout={litellm_timeout_s}s — otherwise a Fargate "
        "scale-in or rolling deploy forcefully closes in-flight non-"
        "streaming requests that LiteLLM is still happy to honor, "
        "surfacing as spurious 504s during deploys (bug #16)"
    )

    # Retry-amplified floor (bug #18).
    retry_total_s = litellm_timeout_s * (1 + litellm_num_retries)
    assert drain_s >= retry_total_s, (
        f"TargetGroup deregistration_delay={drain_s}s must be >= LiteLLM's "
        f"retry-amplified total = timeout({litellm_timeout_s}) × (1 + "
        f"num_retries({litellm_num_retries})) = {retry_total_s}s. "
        "Otherwise an in-flight request whose retry budget extended past "
        "the first attempt is dropped during scale-in (bug #18)."
    )


def test_vllm_container_stop_timeout_covers_litellm_upstream_timeout():
    """Audit-shape regression #21: the vLLM container's ``StopTimeout``
    must be >= LiteLLM's per-attempt upstream timeout.

    ECS stops a task by sending SIGTERM, then SIGKILL after
    ``StopTimeout`` seconds. The default is 30s on EC2 launch type — far
    shorter than a single in-flight decode request, which LiteLLM is
    willing to wait up to ``router_settings.timeout: 600`` for. If a
    vLLM task is replaced (scale-in, rolling deploy, ASG rebalance,
    spot reclaim) mid-decode and StopTimeout < the LiteLLM upstream
    timeout, the in-flight client request is RST'd at SIGTERM+30s
    even though LiteLLM is still happy to wait.

    The retry-amplified outer wait (#18) is irrelevant here: each retry
    is a *new* request to a (possibly different) vLLM target via
    Cloud Map; the container only has to drain a *single* attempt
    cleanly, not the full retry budget.

    Same audit pattern as #15/#16/#18 but for the container's own drain
    rather than the ALB's view of it.
    """
    doc = _load("20-vllm-model.yaml")
    task_def = next(
        r for r in doc["Resources"].values()
        if r["Type"] == "AWS::ECS::TaskDefinition"
    )
    containers = task_def["Properties"]["ContainerDefinitions"]
    vllm = next(c for c in containers if c["Name"] == "vllm")
    stop_timeout_s = vllm.get("StopTimeout")
    assert stop_timeout_s is not None, (
        "vLLM container must set StopTimeout — default 30s is shorter "
        "than LiteLLM's 600s upstream per-attempt timeout"
    )

    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    m_timeout = re.search(r"^\s*timeout:\s*(\d+)\s*$", raw, re.MULTILINE)
    assert m_timeout, "30-litellm-router.yaml must set router_settings.timeout"
    litellm_timeout_s = int(m_timeout.group(1))

    assert int(stop_timeout_s) >= litellm_timeout_s, (
        f"vLLM StopTimeout={stop_timeout_s}s must be >= LiteLLM "
        f"per-attempt upstream timeout={litellm_timeout_s}s — otherwise "
        "an in-flight decode is SIGKILLed during scale-in or task "
        "replacement before LiteLLM's per-attempt budget elapses, "
        "surfacing as spurious 502/504s on the client (bug #21)."
    )

    # Also check the ECS agent's STOP_TIMEOUT is bumped: the agent caps
    # task-definition StopTimeout at 30s by default, regardless of the
    # task definition. Without bumping ECS_CONTAINER_STOP_TIMEOUT in
    # /etc/ecs/ecs.config, the value above is silently truncated.
    cluster_raw = (CFN_DIR / "10-cluster.yaml").read_text()
    m_agent = re.search(
        r"ECS_CONTAINER_STOP_TIMEOUT=(\d+)s", cluster_raw,
    )
    assert m_agent is not None, (
        "10-cluster.yaml must set ECS_CONTAINER_STOP_TIMEOUT in "
        "/etc/ecs/ecs.config — otherwise the agent caps "
        "task-definition StopTimeout at the agent default (30s) and "
        "the StopTimeout=600 above is silently truncated."
    )
    agent_cap_s = int(m_agent.group(1))
    assert agent_cap_s >= int(stop_timeout_s), (
        f"ECS agent's ECS_CONTAINER_STOP_TIMEOUT={agent_cap_s}s must be "
        f">= the task definition's StopTimeout={stop_timeout_s}s, "
        "otherwise the agent silently caps the per-task value."
    )


def test_litellm_container_stop_timeout_at_fargate_max():
    """Audit-shape regression #21 (LiteLLM half): the LiteLLM Fargate
    container's ``StopTimeout`` must be raised from the 30s default
    toward Fargate's 120s cap.

    Fargate hard-caps StopTimeout at 120s, so we can't fully cover the
    1800s retry-amplified deregistration delay; the ALB's
    ``deregistration_delay`` does most of the heavy lifting (it stops
    new connections from being routed to the draining target). But a
    request that arrived just before drain started can still be
    in-flight when ECS sends SIGTERM after the ALB is done. The 30s
    default cuts those off; 120s is the maximum allowed.
    """
    doc = _load("30-litellm-router.yaml")
    task_def = next(
        r for r in doc["Resources"].values()
        if r["Type"] == "AWS::ECS::TaskDefinition"
    )
    containers = task_def["Properties"]["ContainerDefinitions"]
    litellm = next(c for c in containers if c["Name"] == "litellm")
    stop_timeout_s = litellm.get("StopTimeout")
    assert stop_timeout_s is not None, (
        "LiteLLM container must set StopTimeout — default of 30s "
        "drops in-flight requests that were still in-flight when "
        "ECS sent SIGTERM (after the ALB drain window)"
    )
    # Fargate caps at 120s. Anything >= 60s is meaningfully better than
    # the 30s default; we pin to within Fargate's allowed range.
    assert 60 <= int(stop_timeout_s) <= 120, (
        f"LiteLLM Fargate StopTimeout={stop_timeout_s}s must be in "
        "[60, 120] — Fargate caps StopTimeout at 120s, and anything "
        "less than ~60s leaves negligible margin over the 30s default."
    )


def test_litellm_router_resourcelabel_uses_targetgroup_fullname():
    """The ALBRequestCountPerTarget ResourceLabel must be of shape
    'app/<alb-name>/<alb-id>/targetgroup/<tg-name>/<tg-id>'.

    The original template constructed it as
    '<AlbFullName>/targetgroup/<TgName>/<TgUid>' where TgUid was
    `!Select [3, !Split ["/", !Ref TargetGroup]]` — but `!Ref TargetGroup`
    returns the ARN, which only has 3 forward-slash-separated parts (index 0,
    1, 2), so index 3 raises 'list index out of range' at deploy time and
    autoscaling fails to create. The fix uses `!GetAtt
    TargetGroup.TargetGroupFullName` which already returns the
    'targetgroup/<name>/<id>' shape.
    """
    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    assert "TargetGroupFullName" in raw, "must use GetAtt TargetGroupFullName"
    # The buggy form was an index-3 split on the TargetGroup Ref.
    assert "!Select [3, !Split" not in raw
    assert "Select [3, !Split" not in raw  # belt-and-braces
    # And the resource label still references both ALB and target-group halves.
    assert "AlbFullName" in raw
    assert "TgFullName" in raw


# ---------------------------------------------------------------------------
# Cross-template: parameter names align with deployer.ModelService output
# ---------------------------------------------------------------------------
def test_modelservice_params_subset_of_template_parameters():
    from vllm_realtime import ModelService  # noqa: WPS433

    s = ModelService(
        model_name="x", hf_model_id="x/x", served_model_name="x",
        gated=True,
    )
    template_params = set((_load("20-vllm-model.yaml").get("Parameters") or {}).keys())
    sent = {
        p["ParameterKey"]
        for p in s.model_parameters(  # nosec B106
            resource_prefix="rt", vllm_image="img",
            hf_token_secret_arn="a", api_key_secret_arn="b",
        )
    }
    extra = sent - template_params
    assert not extra, f"deployer sends params the template does not declare: {extra}"

    router_template_params = set((_load("30-litellm-router.yaml").get("Parameters") or {}).keys())
    router_sent = {
        p["ParameterKey"]
        for p in s.router_parameters(  # nosec B106
            resource_prefix="rt", litellm_master_key_secret_arn="a",
            api_key_secret_arn="b",
        )
    }
    extra_r = router_sent - router_template_params
    assert not extra_r, f"router deployer sends params the template does not declare: {extra_r}"


# ---------------------------------------------------------------------------
# Per-model resource tagging: every AWS resource
# carries Project=llm-inference + Model=<model-name> tags so cleanup
# automation can sweep by Model. The two per-model templates
# (20-vllm-model.yaml + 30-litellm-router.yaml) are the only place where
# Model is meaningful (00 + 10 are region-shared, no model). Any tagged
# resource in those two templates that does NOT also carry the Model tag
# is a cleanup-discoverability bug — the resource's owner can't be
# determined when sweeping by Model later.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template_name", ["20-vllm-model.yaml", "30-litellm-router.yaml"])
def test_per_model_tagged_resources_carry_both_project_and_model_tag(template_name):
    doc = _load(template_name)
    offenders: list[str] = []
    for logical_id, res in (doc.get("Resources") or {}).items():
        props = res.get("Properties") or {}
        tags = props.get("Tags")
        if not tags:
            # Some resources (IAM roles, scaling targets/policies, secrets
            # blocks, listener rules) don't carry user tags — that's fine;
            # the test only fires for resources whose author *did* declare
            # tags but forgot Model.
            continue
        if isinstance(tags, list):
            keys = {t.get("Key") for t in tags if isinstance(t, dict)}
        else:
            continue
        if "Project" in keys and "Model" not in keys:
            offenders.append(f"{logical_id} ({res['Type']})")
    assert not offenders, (
        f"{template_name}: per-model resources carry Project tag but are "
        f"missing Model=<model-name> — cleanup automation can't sweep by "
        f"Model. Offenders: {offenders}"
    )


def test_litellm_router_settings_include_resilience_and_stream_options():
    """Audit-shape regression: 30-litellm-router.yaml's baked-in
    LITELLM_CONFIG_YAML must include the resilience knobs
    (``allowed_fails`` + ``cooldown_time``) so a flapping vLLM target
    cools off after consecutive failures rather than eating retries on
    every request, AND ``stream_options.include_usage`` so streaming
    clients receive token-usage stats on the final SSE chunk (otherwise
    Prometheus / OpenTelemetry observability sees zero usage on every
    streaming request).

    Best-practice audit: without
    these, the router silently retries against unhealthy targets and
    streaming usage telemetry is lost.
    """
    raw = (CFN_DIR / "30-litellm-router.yaml").read_text()
    # router_settings block — resilience knobs.
    assert re.search(r"^\s*allowed_fails:\s*\d+\s*$", raw, re.MULTILINE), (
        "30-litellm-router.yaml router_settings must declare allowed_fails "
        "(N consecutive failures before a target is cooled off)"
    )
    assert re.search(r"^\s*cooldown_time:\s*\d+\s*$", raw, re.MULTILINE), (
        "30-litellm-router.yaml router_settings must declare cooldown_time "
        "(seconds a target stays cooled off after allowed_fails breaches)"
    )
    # litellm_settings block — streaming usage.
    assert re.search(
        r"stream_options:\s*\n\s*include_usage:\s*true",
        raw,
    ), (
        "30-litellm-router.yaml litellm_settings must set "
        "stream_options.include_usage=true so streaming clients receive "
        "token-usage stats on the final SSE chunk"
    )


def test_litellm_router_sg_descriptions_use_only_legal_chars():
    """AWS rejects SG-rule descriptions containing characters outside
    `a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*`. The arrow ``->`` is a common
    snare — it slips past local lint but fails at deploy time with
    `Invalid rule description`. Pin the legal charset so descriptions
    like `litellm -> vllm:8000` never slip back in.
    """
    import re as _re
    doc = _load("30-litellm-router.yaml")
    legal = _re.compile(r"^[a-zA-Z0-9. _:\-/()#,@\[\]+=&;{}!$*]*$")
    sg_resource_types = {
        "AWS::EC2::SecurityGroup",
        "AWS::EC2::SecurityGroupIngress",
        "AWS::EC2::SecurityGroupEgress",
    }
    found_any = False
    for logical, body in (doc.get("Resources") or {}).items():
        if body.get("Type") not in sg_resource_types:
            continue
        props = body.get("Properties") or {}
        # Description on AWS::EC2::SecurityGroup is GroupDescription;
        # on Ingress/Egress it's Description.
        for key in ("Description", "GroupDescription"):
            value = props.get(key)
            if value is None:
                continue
            # Description may be a string OR a {"Fn::Sub": "..."} dict.
            if isinstance(value, dict) and "Fn::Sub" in value:
                literal = value["Fn::Sub"]
            elif isinstance(value, str):
                literal = value
            else:
                continue
            found_any = True
            stripped = _re.sub(r"\$\{[^}]+\}", "", literal)
            assert legal.fullmatch(stripped), (
                f"{logical}.{key}={literal!r} contains a char outside the "
                f"AWS-legal SG description charset (a-zA-Z0-9. _-:/()#,@[]+=&;{{}}!$*); "
                f"this fails at deploy time with InvalidRequest. Replace e.g. "
                f"'->' with 'to' or '_'."
            )
    assert found_any, "expected at least one SG-related Description in the template"
