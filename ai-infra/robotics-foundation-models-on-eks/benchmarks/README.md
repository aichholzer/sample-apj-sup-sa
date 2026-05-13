# Benchmarks

Benchmarks validate platform behavior, network transport, and distributed
training performance. They are kept outside [examples/](../examples/README.md)
because they are measurement workloads rather than beginner workflow examples.

| Benchmark | Purpose | Output |
| --- | --- | --- |
| [g7e-efa-nccl](g7e-efa-nccl/README.md) | 2-node G7e EFA NCCL all-reduce benchmark. | [Bandwidth plot](g7e-efa-nccl/artifacts/nccl-efa-2node-bandwidth.svg). |
| [g7e-efa-ddp](g7e-efa-ddp/README.md) | 2-node G7e PyTorch DDP training benchmark comparing EFA against NCCL socket networking. | [Training-time plot](g7e-efa-ddp/artifacts/training-time.svg). |
