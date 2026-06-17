# real-time/ecs - vLLM on ECS-on-EC2 + LiteLLM router on Fargate

Production-style real-time inference deployment on AWS:

```
Internet -> ALB (HTTP/HTTPS) -> LiteLLM Fargate -> AWS Cloud Map -> vLLM ECS-on-EC2 GPU tasks
```

## Layout

```
real-time/ecs/
├── cfn/
│   ├── 00-networking.yaml     # VPC, subnets, ALB, Cloud Map (one per region)
│   ├── 10-cluster.yaml        # ECS cluster + 2 ASGs (spot + on-demand) + 2 capacity providers
│   ├── 20-vllm-model.yaml     # vLLM task definition + ECS service (one per model)
│   └── 30-litellm-router.yaml # LiteLLM router (Fargate) + ALB target group + listener rule
├── src/vllm_realtime/         # Python helper (deployer, secrets, load tests)
├── models/{qwen3_8b, medgemma_27b, llama_4_scout_17b}/
│                              # Per-model ModelService config
├── notebooks/                 # End-to-end notebook(s)
└── tests/
```

## Stacks

* **00-networking** is deployed once per region. It exports VPC id, subnet
  ids, ALB ARN/DNS, ALB security group, and a Cloud Map private DNS
  namespace named `llm-inference-rt.local`.
* **10-cluster** is deployed once per region. It creates the ECS cluster,
  two GPU ASGs (price-capacity-optimized spot + plain on-demand), and two
  capacity providers wired with `EnableManagedScaling=ENABLED` and
  `EnableManagedTerminationProtection=ENABLED`. Default cluster strategy is
  4:1 spot:on-demand.
* **20-vllm-model** is deployed once per model. It creates an ECS-on-EC2
  service that serves vLLM behind Cloud Map.
* **30-litellm-router** is deployed once per model. It runs the LiteLLM
  proxy on Fargate, fronts it with an ALB target group + listener rule,
  and resolves vLLM tasks via `http://vllm-<model>.llm-inference-rt.local:8000/v1`
  (Cloud Map MULTIVALUE A records, TTL=10). The `ListenerProtocol`
  parameter (default `HTTP`) selects which ALB listener the rule attaches
  to; pass `HTTPS` for production deploys (requires the networking stack
  to have been deployed with `AlbCertificateArn` set).

## Per-model ModelService

Each `models/<name>/__init__.py` exports a `SERVICE: ModelService` object
that captures the vLLM packing decisions (TP/DP, GPU count, `max_model_len`,
extra serve flags) and the prompts to use during smoke + load tests.

* `qwen3_8b` -- 1 GPU, no gating. Set cluster `InstanceType` to a
  single-GPU SKU like `g7e.2xlarge` or `g6e.2xlarge`.
* `medgemma_27b` -- 1 GPU, gated (HF token required). Deploy on
  `g7e.2xlarge` (Blackwell 96 GiB) for headroom.
* `llama_4_scout_17b` -- 8 GPUs (TP=8), gated, requires
  `--kv-cache-dtype fp8` to fit a 32K context on `p4d.24xlarge`.

## Notebook flow (real-time-ecs.ipynb)

1. Imports + AWS identity
2. Pick model -> select `SERVICE`
3. Deploy networking stack (one-time per region)
4. Deploy cluster stack (one-time per region)
5. Upsert HF token + vLLM API key + LiteLLM master key in Secrets Manager
6. Deploy vLLM-model stack
7. Deploy LiteLLM router stack
8. Wait for ALB target healthy
9. Curl smoke test
10. Async load test (200 requests at concurrency=16)
11. Teardown stacks (commented)

## Tear-down order

```
30-litellm-router  (per model)
20-vllm-model      (per model)
10-cluster         (per region; only when no models remain)
00-networking      (per region; only when 10 is gone)
```

## Tear-down prerequisite — GuardDuty VPC endpoint

If your AWS account has GuardDuty VPC monitoring enabled, an AWS-managed
VPC interface endpoint is auto-attached to every new VPC. The endpoint
is owned by AWS and is **not** described by this stack, but it pins ENIs
in the subnets and blocks `00-networking` deletion with
`DependencyViolation`.

Before deleting `00-networking`, sweep any GuardDuty endpoints from the
VPC. The endpoint name pattern is `vpce-svc-*` with service name like
`com.amazonaws.<region>.guardduty-data`:

```bash
aws ec2 describe-vpc-endpoints --region "$AWS_REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" \
            "Name=service-name,Values=com.amazonaws.$AWS_REGION.guardduty-data" \
  --query 'VpcEndpoints[].VpcEndpointId' --output text \
| xargs -r aws ec2 delete-vpc-endpoints --region "$AWS_REGION" --vpc-endpoint-ids
```

Then retry `cfn delete-stack`. The ENIs release within ~60s.
