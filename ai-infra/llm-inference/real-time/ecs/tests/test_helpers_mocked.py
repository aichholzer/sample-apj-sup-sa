"""Tests for upsert_secret + load_test that don't need real AWS / HTTP."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vllm_realtime.deployer import (  # noqa: E402
    _stack_exists,
    _stack_in_unrecoverable_state,
    deploy_stack,
    upsert_secret,
)
from vllm_realtime.load_test import async_load_test, smoke_test_endpoint  # noqa: E402


# ---------------------------------------------------------------------------
# _stack_exists — DELETE_COMPLETE stays queryable in CFN for ~90 days, but
# must be treated as "doesn't exist" so deploy_stack picks create_stack().
# ---------------------------------------------------------------------------
def test_stack_exists_true_for_create_complete():
    cfn = MagicMock()
    cfn.describe_stacks.return_value = {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]}
    assert _stack_exists(cfn, "my-stack") is True


def test_stack_exists_false_for_delete_complete():
    cfn = MagicMock()
    cfn.describe_stacks.return_value = {"Stacks": [{"StackStatus": "DELETE_COMPLETE"}]}
    assert _stack_exists(cfn, "my-stack") is False


def test_stack_exists_false_when_describe_raises_does_not_exist():
    from botocore.exceptions import ClientError
    cfn = MagicMock()
    cfn.describe_stacks.side_effect = ClientError(
        {"Error": {"Code": "ValidationError",
                   "Message": "Stack with id my-stack does not exist"}},
        "DescribeStacks",
    )
    assert _stack_exists(cfn, "my-stack") is False


@pytest.mark.parametrize("status", [
    "ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "CREATE_FAILED", "DELETE_FAILED",
    "UPDATE_ROLLBACK_FAILED",
])
def test_stack_exists_false_for_unrecoverable_states(status):
    """CFN rejects update_stack() on these states. Treating them as 'exists'
    would route deploy_stack to update_stack and fail. Treating them as
    'doesn't exist' AND 'requires-delete-first' lets deploy_stack delete +
    recreate. (DELETE_FAILED still occupies the stack-name slot — must be
    deleted before a fresh create.)

    UPDATE_ROLLBACK_FAILED happens when an update + its auto-rollback both
    fail; the slot remains occupied and update_stack() is rejected, so
    deploy_stack must take the same delete + recreate path as ROLLBACK_FAILED.
    """
    cfn = MagicMock()
    cfn.describe_stacks.return_value = {"Stacks": [{"StackStatus": status}]}
    assert _stack_exists(cfn, "my-stack") is False
    assert _stack_in_unrecoverable_state(cfn, "my-stack") is True


def test_stack_in_unrecoverable_state_false_for_create_complete():
    cfn = MagicMock()
    cfn.describe_stacks.return_value = {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]}
    assert _stack_in_unrecoverable_state(cfn, "my-stack") is False


def test_stack_in_unrecoverable_state_false_for_delete_complete():
    """DELETE_COMPLETE is a separate case — _stack_exists returns False,
    and the slot is free for create_stack without an explicit delete first."""
    cfn = MagicMock()
    cfn.describe_stacks.return_value = {"Stacks": [{"StackStatus": "DELETE_COMPLETE"}]}
    assert _stack_in_unrecoverable_state(cfn, "my-stack") is False


def _make_does_not_exist_error():
    from botocore.exceptions import ClientError
    return ClientError(
        {"Error": {"Code": "ValidationError",
                   "Message": "Stack with id x does not exist"}},
        "DescribeStacks",
    )


def test_deploy_stack_waiter_max_attempts_scales_with_timeout(tmp_path, monkeypatch):
    """The CFN waiter timeout must be settable per-call. Llama-4-Scout's
    20-vllm-model.yaml stack can hold AWS::ECS::Service in CREATE_IN_PROGRESS
    for up to ~90 min (218 GiB BF16 weights from HF + ASG provisioning + image
    pull + warmup); a fixed 60-min timeout would raise WaiterError while CFN
    is still creating. Ensure the timeout flows into the waiter config.
    """
    cfn = MagicMock()
    # 1: _stack_exists -> ValidationError -> False
    # 2: _stack_in_unrecoverable_state -> ValidationError -> False (skip delete)
    # 3: _stack_outputs after create
    cfn.describe_stacks.side_effect = [
        _make_does_not_exist_error(),
        _make_does_not_exist_error(),
        {"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": []}]},
    ]
    create_waiter = MagicMock()
    cfn.get_waiter.return_value = create_waiter
    monkeypatch.setattr("boto3.client", lambda *a, **k: cfn)

    template = tmp_path / "t.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")

    deploy_stack(stack_name="x", template_path=template, region="us-west-2",
                 poll_seconds=15, wait_timeout_s=7200)

    create_waiter.wait.assert_called_once()
    cfg = create_waiter.wait.call_args.kwargs["WaiterConfig"]
    assert cfg["Delay"] == 15
    # 7200 / 15 == 480 attempts → ~2h tolerance, enough for Llama-4-Scout.
    assert cfg["MaxAttempts"] == 480, (
        f"wait_timeout_s=7200 with Delay=15 must produce MaxAttempts=480, got {cfg}"
    )


def test_deploy_stack_default_waiter_max_attempts_is_one_hour(tmp_path, monkeypatch):
    """Default wait timeout is 3600s — the common case for the small stacks
    (networking, cluster, smaller-model 20-vllm-model, 30-litellm-router).
    """
    cfn = MagicMock()
    cfn.describe_stacks.side_effect = [
        _make_does_not_exist_error(),
        _make_does_not_exist_error(),
        {"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": []}]},
    ]
    create_waiter = MagicMock()
    cfn.get_waiter.return_value = create_waiter
    monkeypatch.setattr("boto3.client", lambda *a, **k: cfn)

    template = tmp_path / "t.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")

    deploy_stack(stack_name="x", template_path=template, region="us-west-2")
    cfg = create_waiter.wait.call_args.kwargs["WaiterConfig"]
    assert cfg["MaxAttempts"] == 240  # 3600s / 15s = 240


def test_deploy_stack_deletes_rollback_complete_before_create(tmp_path, monkeypatch):
    """If a previous create failed and left the stack in ROLLBACK_COMPLETE,
    deploy_stack must delete the failed stack before issuing create_stack —
    otherwise CFN returns AlreadyExistsException."""
    cfn = MagicMock()
    # describe_stacks is called once by _stack_exists (returns ROLLBACK_COMPLETE,
    # so _stack_exists returns False) and again by _stack_in_unrecoverable_state
    # (returns ROLLBACK_COMPLETE → True). Then again by _stack_outputs.
    cfn.describe_stacks.side_effect = [
        {"Stacks": [{"StackStatus": "ROLLBACK_COMPLETE"}]},
        {"Stacks": [{"StackStatus": "ROLLBACK_COMPLETE"}]},
        {"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": []}]},
    ]
    delete_waiter = MagicMock()
    create_waiter = MagicMock()

    def _get_waiter(name):
        if name == "stack_delete_complete":
            return delete_waiter
        return create_waiter

    cfn.get_waiter.side_effect = _get_waiter
    monkeypatch.setattr("boto3.client", lambda *a, **k: cfn)

    template = tmp_path / "t.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")

    deploy_stack(stack_name="x", template_path=template, region="us-west-2",
                 poll_seconds=1)
    assert cfn.delete_stack.called, "ROLLBACK_COMPLETE stack must be deleted before create"
    assert cfn.create_stack.called, "fresh create_stack must follow the delete"
    delete_waiter.wait.assert_called_once()


def test_deploy_stack_retries_waiter_does_not_exist_race(tmp_path, monkeypatch):
    """boto3's StackCreateComplete waiter
    misclassifies the transient ValidationError that describe_stacks returns
    between create_stack returning and CFN actually registering the stack.
    The deployer must retry instead of bubbling that as a terminal failure.
    """
    from botocore.exceptions import WaiterError
    cfn = MagicMock()
    cfn.describe_stacks.side_effect = [
        _make_does_not_exist_error(),  # _stack_exists
        _make_does_not_exist_error(),  # _stack_in_unrecoverable_state
        {"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": []}]},
    ]
    create_waiter = MagicMock()
    # First wait raises the race, second wait succeeds.
    create_waiter.wait.side_effect = [
        WaiterError(
            name="StackCreateComplete",
            reason="Matched expected service error code: ValidationError",
            last_response={"Error": {"Code": "ValidationError",
                                     "Message": "Stack [x] does not exist"}},
        ),
        None,
    ]
    cfn.get_waiter.return_value = create_waiter
    monkeypatch.setattr("boto3.client", lambda *a, **k: cfn)
    monkeypatch.setattr("vllm_realtime.deployer.time.sleep", lambda *_: None)

    template = tmp_path / "t.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")

    deploy_stack(stack_name="x", template_path=template, region="us-west-2",
                 poll_seconds=1)
    assert create_waiter.wait.call_count == 2, (
        "first wait must retry on the does-not-exist race; second must succeed"
    )


def test_deploy_stack_does_not_swallow_real_waiter_failures(tmp_path, monkeypatch):
    """Only the does-not-exist race retries — every other WaiterError must
    bubble so a genuine CFN failure is surfaced.
    """
    from botocore.exceptions import WaiterError
    cfn = MagicMock()
    cfn.describe_stacks.side_effect = [
        _make_does_not_exist_error(),
        _make_does_not_exist_error(),
    ]
    create_waiter = MagicMock()
    create_waiter.wait.side_effect = WaiterError(
        name="StackCreateComplete",
        reason="Matched expected service status: CREATE_FAILED",
        last_response={"Stacks": [{"StackStatus": "CREATE_FAILED"}]},
    )
    cfn.get_waiter.return_value = create_waiter
    monkeypatch.setattr("boto3.client", lambda *a, **k: cfn)
    monkeypatch.setattr("vllm_realtime.deployer.time.sleep", lambda *_: None)

    template = tmp_path / "t.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")

    with pytest.raises(WaiterError):
        deploy_stack(stack_name="x", template_path=template,
                     region="us-west-2", poll_seconds=1)


# ---------------------------------------------------------------------------
# upsert_secret — exercises both create and put-existing branches
# ---------------------------------------------------------------------------
def test_upsert_secret_create(monkeypatch):
    sm = MagicMock()
    sm.create_secret.return_value = {"ARN": "arn:aws:secretsmanager:us-west-2:1:secret:n"}

    monkeypatch.setattr("boto3.client", lambda *a, **k: sm)

    arn = upsert_secret(name="n", value="v", region="us-west-2", description="d")
    assert arn == "arn:aws:secretsmanager:us-west-2:1:secret:n"
    sm.create_secret.assert_called_once()
    sm.put_secret_value.assert_not_called()

    # No model_name passed → only Project tag.
    tags = sm.create_secret.call_args.kwargs["Tags"]
    keys = {t["Key"] for t in tags}
    assert keys == {"Project"}


def test_upsert_secret_create_with_model_name_adds_model_tag(monkeypatch):
    """Passing model_name must add the Model tag so cleanup automation can
    sweep per-model secrets along with the rest of the model's resources.
    """
    sm = MagicMock()
    sm.create_secret.return_value = {"ARN": "arn:aws:secretsmanager:us-west-2:1:secret:n"}
    monkeypatch.setattr("boto3.client", lambda *a, **k: sm)

    upsert_secret(name="n", value="v", model_name="qwen3-8b")

    tags = {t["Key"]: t["Value"] for t in sm.create_secret.call_args.kwargs["Tags"]}
    assert tags["Project"] == "llm-inference"
    assert tags["Model"] == "qwen3-8b"


def test_upsert_secret_existing(monkeypatch):
    from botocore.exceptions import ClientError
    sm = MagicMock()
    sm.create_secret.side_effect = ClientError(
        {"Error": {"Code": "ResourceExistsException", "Message": "exists"}},
        "CreateSecret",
    )
    sm.describe_secret.return_value = {"ARN": "arn:aws:secretsmanager:us-west-2:1:secret:n"}

    monkeypatch.setattr("boto3.client", lambda *a, **k: sm)

    arn = upsert_secret(name="n", value="v")
    assert arn.endswith(":secret:n")
    sm.put_secret_value.assert_called_once_with(SecretId="n", SecretString="v")


# ---------------------------------------------------------------------------
# async_load_test — substitute httpx.AsyncClient with a fake
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def post(self, url, json=None, headers=None):
        text_in = json["messages"][-1]["content"]
        return _FakeResp(200, {
            "choices": [{"message": {"content": f"reply:{text_in}"}}],
            "usage": {"completion_tokens": 32},
        })


def test_async_load_test_aggregates(monkeypatch):
    import vllm_realtime.load_test as lt  # noqa: WPS433
    monkeypatch.setattr(lt.httpx, "AsyncClient", _FakeAsyncClient)

    inputs = ["hello world"] * 10
    res = asyncio.run(async_load_test(
        base_url="http://router.example",
        api_key="k",
        served_model_name="qwen3-8b",
        system_prompt="sys",
        user_inputs=inputs,
        concurrency=4,
        max_tokens=32,
    ))
    assert res.n_requests == 10
    assert res.n_success == 10
    assert res.n_failed == 0
    assert res.output_tokens == 32 * 10
    assert res.elapsed_s > 0
    assert res.output_throughput_tok_s > 0


# ---------------------------------------------------------------------------
# smoke_test_endpoint — patch httpx.Client
# ---------------------------------------------------------------------------
class _FakeSyncClient:
    def __init__(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def post(self, url, json=None, headers=None):
        return _FakeSyncResp({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"completion_tokens": 4},
        })


class _FakeSyncResp:
    def __init__(self, body):
        self._body = body
        self.status_code = 200
    def raise_for_status(self): pass
    def json(self): return self._body


def test_smoke_test_endpoint(monkeypatch):
    import vllm_realtime.load_test as lt  # noqa: WPS433
    monkeypatch.setattr(lt.httpx, "Client", _FakeSyncClient)
    out = smoke_test_endpoint(
        base_url="http://router.example/",
        api_key="k",
        served_model_name="qwen3-8b",
        system_prompt="sys",
        user_input="hi",
    )
    assert out["choices"][0]["message"]["content"] == "ok"


def test_async_load_test_default_timeout_covers_litellm_retry_budget():
    """Audit-shape regression #20: async_load_test's default httpx
    ``timeout_s`` must be >= LiteLLM's retry-amplified per-request total
    (``router_settings.timeout × (1 + num_retries)``).

    LiteLLM's router (30-litellm-router.yaml) has
    ``num_retries: 2`` and ``timeout: 600``, so a single non-streaming
    request can occupy LiteLLM's upstream wait for up to 1800s on the
    worst tail. If the client-side httpx timeout is shorter than that,
    the load test misclassifies healthy long-tail requests as failures
    while LiteLLM is still on retry attempt 2 or 3 — the same shape
    that bug #18 fixed for the ALB / TG drain.

    Pin the relationship: the default load-test timeout must be
    >= ``timeout × (1 + num_retries)`` parsed from the router config.
    """
    import inspect
    import re
    from pathlib import Path
    from vllm_realtime.load_test import async_load_test

    sig = inspect.signature(async_load_test)
    default_timeout = sig.parameters["timeout_s"].default

    cfn_dir = Path(__file__).resolve().parents[1] / "cfn"
    raw = (cfn_dir / "30-litellm-router.yaml").read_text()
    m_timeout = re.search(r"^\s*timeout:\s*(\d+)\s*$", raw, re.MULTILINE)
    m_retries = re.search(r"^\s*num_retries:\s*(\d+)\s*$", raw, re.MULTILINE)
    assert m_timeout and m_retries, (
        "30-litellm-router.yaml must declare router_settings.timeout "
        "and num_retries"
    )
    retry_total_s = int(m_timeout.group(1)) * (1 + int(m_retries.group(1)))

    assert default_timeout >= retry_total_s, (
        f"async_load_test default timeout_s={default_timeout}s must be "
        f">= LiteLLM retry-amplified total = {retry_total_s}s "
        f"(timeout {m_timeout.group(1)} × (1 + num_retries "
        f"{m_retries.group(1)})). Otherwise the load-test client "
        "times out and reports false failures while LiteLLM is still "
        "honoring its retry budget (bug #20)."
    )


# ---------------------------------------------------------------------------
# sweep_guardduty_vpc_endpoints — bug #23: GuardDuty VPC monitoring auto-
# attaches an AWS-managed interface endpoint to every new VPC; the endpoint
# pins ENIs in the subnets and blocks 00-networking teardown with
# DependencyViolation. Caller must sweep them before teardown_stack().
# ---------------------------------------------------------------------------
def test_sweep_guardduty_vpc_endpoints_deletes_matching():
    from vllm_realtime.deployer import sweep_guardduty_vpc_endpoints

    ec2 = MagicMock()
    ec2.describe_vpc_endpoints.return_value = {
        "VpcEndpoints": [
            {"VpcEndpointId": "vpce-aaa111"},
            {"VpcEndpointId": "vpce-bbb222"},
        ]
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}

    deleted = sweep_guardduty_vpc_endpoints(
        vpc_id="vpc-1234",
        region="us-west-2",
        ec2_client=ec2,
    )

    assert deleted == ["vpce-aaa111", "vpce-bbb222"]

    # Filters must scope to (a) the VPC and (b) the GuardDuty data
    # service in the target region — sweeping a generic vpc-id-only
    # filter would clobber any other endpoints (S3 gateway, etc.).
    call = ec2.describe_vpc_endpoints.call_args
    filters = {f["Name"]: f["Values"] for f in call.kwargs["Filters"]}
    assert filters["vpc-id"] == ["vpc-1234"]
    assert filters["service-name"] == ["com.amazonaws.us-west-2.guardduty-data"]

    ec2.delete_vpc_endpoints.assert_called_once_with(
        VpcEndpointIds=["vpce-aaa111", "vpce-bbb222"],
    )


def test_sweep_guardduty_vpc_endpoints_noop_when_none():
    from vllm_realtime.deployer import sweep_guardduty_vpc_endpoints

    ec2 = MagicMock()
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}

    deleted = sweep_guardduty_vpc_endpoints(
        vpc_id="vpc-1234",
        region="us-east-1",
        ec2_client=ec2,
    )

    assert deleted == []
    ec2.delete_vpc_endpoints.assert_not_called()


def test_sweep_guardduty_vpc_endpoints_uses_region_in_service_name():
    """Service name must follow the region — sweeping us-west-2's data
    endpoint with us-east-1's filter would silently miss the target."""
    from vllm_realtime.deployer import sweep_guardduty_vpc_endpoints

    for region in ("us-west-2", "us-east-1", "us-east-2"):
        ec2 = MagicMock()
        ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
        ec2.describe_security_groups.return_value = {"SecurityGroups": []}
        sweep_guardduty_vpc_endpoints(
            vpc_id="vpc-1234", region=region, ec2_client=ec2,
        )
        call = ec2.describe_vpc_endpoints.call_args
        filters = {f["Name"]: f["Values"] for f in call.kwargs["Filters"]}
        assert filters["service-name"] == [
            f"com.amazonaws.{region}.guardduty-data"
        ]


def test_sweep_guardduty_also_deletes_managed_security_group():
    """GuardDuty Runtime Monitoring (account-level setting) auto-attaches a
    managed security group named ``GuardDutyManagedSecurityGroup-<vpc-id>``
    to the VPC. That SG holds a dependency on the VPC, blocking the
    networking stack delete with ``DependencyViolation`` even after all
    user resources are torn down. Sweep deletes it alongside the VPC
    endpoints.
    """
    from vllm_realtime.deployer import sweep_guardduty_vpc_endpoints

    ec2 = MagicMock()
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    ec2.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-deadbeef"}]
    }

    deleted = sweep_guardduty_vpc_endpoints(
        vpc_id="vpc-1234", region="us-west-2", ec2_client=ec2,
    )

    sg_call = ec2.describe_security_groups.call_args
    filters = {f["Name"]: f["Values"] for f in sg_call.kwargs["Filters"]}
    assert filters["vpc-id"] == ["vpc-1234"]
    assert filters["group-name"] == [
        "GuardDutyManagedSecurityGroup-vpc-1234"
    ]
    ec2.delete_security_group.assert_called_once_with(GroupId="sg-deadbeef")
    assert "sg-deadbeef" in deleted


# ---------------------------------------------------------------------------
# Audit fix #19 — teardown_stack auto-recovers DELETE_FAILED via
# RetainResources for the stuck Service. Otherwise the orphan ALB target
# group + listener rule survive and block the next deployment.
# ---------------------------------------------------------------------------
def test_teardown_stack_retains_delete_failed_service():
    """When the first delete_stack waiter raises (DELETE_FAILED), teardown
    inspects describe_stack_resources, finds the failed logical id (the
    ECS Service), and retries delete_stack with RetainResources=[that id].
    """
    from botocore.exceptions import WaiterError
    from vllm_realtime.deployer import teardown_stack

    cfn = MagicMock()
    cfn.describe_stacks.return_value = {
        "Stacks": [{"StackStatus": "CREATE_COMPLETE"}]
    }
    waiter1 = MagicMock()
    waiter1.wait.side_effect = WaiterError(
        name="StackDeleteComplete",
        reason="DELETE_FAILED",
        last_response={"Error": {"Code": "DELETE_FAILED",
                                 "Message": "Service failed to delete"}},
    )
    waiter2 = MagicMock()  # second waiter (post-retry) succeeds
    cfn.get_waiter.side_effect = [waiter1, waiter2]
    cfn.describe_stack_resources.return_value = {
        "StackResources": [
            {"LogicalResourceId": "TaskDef", "ResourceStatus": "DELETE_COMPLETE"},
            {"LogicalResourceId": "Service", "ResourceStatus": "DELETE_FAILED"},
        ],
    }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("vllm_realtime.deployer.boto3.client",
                   MagicMock(return_value=cfn))
        teardown_stack(stack_name="llm-rt-litellm-foo", region="us-west-2")

    # Two delete_stack calls: first plain, second with RetainResources.
    assert cfn.delete_stack.call_count == 2
    first = cfn.delete_stack.call_args_list[0]
    second = cfn.delete_stack.call_args_list[1]
    assert first.kwargs == {"StackName": "llm-rt-litellm-foo"}
    assert second.kwargs == {
        "StackName": "llm-rt-litellm-foo",
        "RetainResources": ["Service"],
    }


def test_teardown_stack_retain_only_failed_resources():
    """RetainResources should be JUST the DELETE_FAILED logical ids — NOT
    DELETE_COMPLETE / DELETE_IN_PROGRESS resources. Otherwise CFN would
    abandon them on the second pass and they'd survive cleanup."""
    from botocore.exceptions import WaiterError
    from vllm_realtime.deployer import teardown_stack

    cfn = MagicMock()
    cfn.describe_stacks.return_value = {
        "Stacks": [{"StackStatus": "CREATE_COMPLETE"}]
    }
    cfn.get_waiter.side_effect = [
        MagicMock(wait=MagicMock(side_effect=WaiterError(
            name="StackDeleteComplete", reason="DELETE_FAILED",
            last_response={"Error": {}},
        ))),
        MagicMock(),
    ]
    cfn.describe_stack_resources.return_value = {
        "StackResources": [
            {"LogicalResourceId": "Service", "ResourceStatus": "DELETE_FAILED"},
            {"LogicalResourceId": "TargetGroup",
             "ResourceStatus": "DELETE_COMPLETE"},
            {"LogicalResourceId": "ListenerRule",
             "ResourceStatus": "DELETE_COMPLETE"},
            {"LogicalResourceId": "TaskRole",
             "ResourceStatus": "DELETE_IN_PROGRESS"},
        ],
    }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("vllm_realtime.deployer.boto3.client",
                   MagicMock(return_value=cfn))
        teardown_stack(stack_name="llm-rt-litellm-foo", region="us-west-2")

    second = cfn.delete_stack.call_args_list[1]
    assert second.kwargs["RetainResources"] == ["Service"]
    # critical: orphan TargetGroup + ListenerRule are NOT retained
    assert "TargetGroup" not in second.kwargs["RetainResources"]
    assert "ListenerRule" not in second.kwargs["RetainResources"]


def test_teardown_stack_succeeds_first_try_no_retry():
    """When the first delete completes cleanly, no RetainResources retry
    happens — the second delete_stack call must NOT be made."""
    from vllm_realtime.deployer import teardown_stack

    cfn = MagicMock()
    cfn.describe_stacks.return_value = {
        "Stacks": [{"StackStatus": "CREATE_COMPLETE"}]
    }
    cfn.get_waiter.return_value = MagicMock()  # waiter.wait() returns cleanly

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("vllm_realtime.deployer.boto3.client",
                   MagicMock(return_value=cfn))
        teardown_stack(stack_name="llm-rt-vllm-foo", region="us-west-2")

    assert cfn.delete_stack.call_count == 1
    cfn.describe_stack_resources.assert_not_called()
