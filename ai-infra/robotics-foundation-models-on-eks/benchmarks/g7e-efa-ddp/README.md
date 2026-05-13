# G7e EFA DDP Benchmark

Kubernetes-native 2-node PyTorch DDP benchmark for comparing EFA-backed NCCL
against NCCL socket networking on the same G7e pool.

This benchmark is intentionally synthetic and communication-heavy. It measures
training step wall-clock for a DDP model with a large gradient payload, not model
quality or end-to-end dataset throughput. Use it when the question is whether
EFA changes multi-node training time for gradient-synchronization-heavy jobs.

Prerequisites:

- `infra/kubernetes/deploy-karpenter.sh` has deployed the G7e NodePool.
- `infra/kubernetes/deploy-efa-device-plugin.sh` has deployed the AWS EFA device plugin.
- `infra/core` has applied the node security group self ingress and egress rules
  required by EFA.
- At least two EFA-capable G7e nodes can be provisioned. The EFA mode requests
  `vpc.amazonaws.com/efa: 1`, and both modes request one GPU per pod.
- If On-Demand G7e capacity is scarce and Spot is acceptable, redeploy
  Karpenter with
  `KARPENTER_CAPACITY_TYPES=on-demand,spot infra/kubernetes/deploy-karpenter.sh`.
- For repeatable validation, use a targeted EC2 Capacity Reservation or Capacity
  Block and redeploy Karpenter with
  `KARPENTER_CAPACITY_RESERVATION_IDS=cr-... infra/kubernetes/deploy-karpenter.sh`.

Run:

```bash
KUBE_CONTEXT=example-osmo-context \
  benchmarks/g7e-efa-ddp/run.sh
```

`run.sh` renders the Kubernetes objects from [templates/](templates/) and keeps
the training workload itself in [train.py](train.py). This makes the pod shape
reviewable without reading the runner script.

The runner executes two modes with the same PyTorch training script:

- `efa`: requests `vpc.amazonaws.com/efa: 1` and lets NCCL use the AWS OFI
  NCCL/Libfabric path.
- `socket`: does not request EFA and sets `NCCL_NET=Socket` so NCCL uses the
  ordinary pod network path.

Default workload:

- 2 nodes
- 1 GPU per node
- 256 MiB gradient payload per rank
- 2 warmup steps
- 12 measured training steps

Representative output:

- [Training-time plot](artifacts/training-time.svg)
