# Fine-tuning on EKS

A reusable infrastructure scaffold for distributed LLM fine-tuning on Amazon EKS using KubeRay, Karpenter, and NVIDIA GPUs.

## Overview

This project provides a complete, production-ready setup for Supervised Fine-Tuning (SFT) of large language models on Kubernetes. It supports:

- **Multiple model sizes**: TinyLlama-1.1B to Llama-70B
- **Flexible resource allocation**: Single GPU, multi-GPU, or multi-node training
- **Training methods**: QLoRA (4-bit), LoRA, DDP, FSDP
- **Cost optimization**: Karpenter auto-scales GPU nodes to zero when idle, prefers Spot instances

## Architecture

```
                        ┌─────────────────────────────────────────────────────────┐
                        │                         VPC                             │
                        │  ┌─────────────────┐    ┌─────────────────────────────┐ │
                        │  │  Public Subnet  │    │      Private Subnets        │ │
                        │  │    (NAT GW)     │    │                             │ │
                        │  └─────────────────┘    │   ┌───────────────────────┐ │ │
                        │                         │   │      EKS Cluster      │ │ │
                        │                         │   │                       │ │ │
┌────────────────┐      │                         │   │  ┌─────────────────┐  │ │ │
│  setup.sh      │──────┼─────────────────────────┼───┼─▶│    KubeRay      │  │ │ │
│  (CLI)         │      │                         │   │  │    Operator     │  │ │ │
└────────────────┘      │                         │   │  └────────┬────────┘  │ │ │
                        │                         │   │           │           │ │ │
                        │                         │   │  ┌────────▼────────┐  │ │ │
                        │  ┌─────────────────┐    │   │  │     Kueue       │  │ │ │
                        │  │   Karpenter     │◀───┼───┼──│ (Gang Schedule) │  │ │ │
                        │  │  (Auto-scale)   │    │   │  └────────┬────────┘  │ │ │
                        │  └─────────────────┘    │   │           │           │ │ │
                        │                         │   │  ┌────────▼────────┐  │ │ │
                        │                         │   │  │    RayJob       │  │ │ │
                        │                         │   │  │  (Training)     │  │ │ │
                        │                         │   │  └────────┬────────┘  │ │ │
                        │                         │   │           │           │ │ │
                        │                         │   │  ┌────────▼────────┐  │ │ │
                        │                         │   │  │  GPU Workers    │  │ │ │
                        │                         │   │  │ (g5/g6e/g7e/p5) │  │ │ │
                        │                         │   │  └─────────────────┘  │ │ │
                        │                         │   │                       │ │ │
                        │  ┌─────────────────┐    │   └───────────────────────┘ │ │
                        │  │      EFS        │◀───┼─── Shared storage (HF cache)│ │
                        │  │ (Shared Storage)│    │                             │ │
                        │  └─────────────────┘    └─────────────────────────────┘ │
                        └─────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                        ┌─────────────────────────────────────────────────────────┐
                        │                     Amazon S3                           │
                        │  ┌──────────────────┐  ┌──────────────────────────────┐ │
                        │  │ /ray/checkpoints │  │    /outputs/<model>/<ts>/    │ │
                        │  │  (Fault Tolerance) │  │      (Final Models)         │ │
                        │  └──────────────────┘  └──────────────────────────────┘ │
                        └─────────────────────────────────────────────────────────┘
```

## Components

| Component | Purpose |
|-----------|---------|
| **EKS** | Managed Kubernetes cluster |
| **Karpenter** | Auto-provisions GPU nodes on demand (Spot preferred for g-family, Capacity Blocks for p-family), scales to zero when idle |
| **KubeRay** | Manages Ray clusters for distributed training |
| **Kueue** | Gang scheduling for atomic worker allocation (prevents deadlocks) |
| **EFS** | Shared filesystem for HuggingFace model cache across nodes |
| **S3** | Distributed checkpoints and model outputs with fault tolerance |
| **NVIDIA GPU Operator** | Manages GPU node components (device plugin, DCGM exporter) |

## Project Structure

```
fine-tuning-on-eks/
├── terraform/                    # Infrastructure as Code
│   ├── environments/
│   │   ├── dev/                  # Full deployment (VPC + EKS + add-ons)
│   │   └── existing-cluster/    # Add-ons only (for existing EKS clusters)
│   └── modules/
│       ├── vpc/                  # VPC with public/private subnets
│       ├── eks/                  # EKS cluster configuration
│       ├── karpenter/            # Karpenter for GPU auto-scaling
│       ├── kuberay/              # KubeRay operator (Helm)
│       ├── efs/                  # EFS filesystem + CSI driver
│       ├── s3/                   # S3 bucket for checkpoints + IRSA
│       └── gpu-nodegroup/        # (Legacy) Static GPU node group
│
├── kubernetes/                   # Kubernetes manifests (Kustomize)
│   ├── base/
│   │   ├── ray/                  # Base RayJob, ConfigMap, ServiceAccount, PVCs
│   │   └── kueue/                # Kueue ResourceFlavors and Queues
│   ├── components/               # Reusable configuration components
│   │   ├── image/                # Container image config
│   │   ├── training/             # Training method (qlora, lora, fsdp)
│   │   ├── scheduling/kueue/     # Gang scheduling integration
│   │   ├── storage/s3/           # S3 storage for checkpoints/outputs
│   │   └── settings/             # Training duration (quick-test, full-training)
│   └── overlays/                 # Model-specific overlays (all config inline)
│       ├── tinyllama-1b/         # 2x RTX 6000, lora+DDP
│       ├── llama-7b/             # 1x A10G, qlora
│       ├── llama-13b/            # 4x A10G, lora+DDP
│       └── llama-70b/            # 8x A10G, fsdp
│
├── src/training/                 # Training code
│   ├── core.py                   # Shared training core
│   ├── train.py                  # Standalone training entry point
│   └── train_ray.py              # Ray Train distributed wrapper
│
├── docker/                       # Container build
│   ├── Dockerfile
│   └── build.sh                  # Build and push to ECR
│
└── scripts/
    └── setup.sh                  # Main CLI for all operations
```

### Terraform Structure

The `terraform/` directory follows a module-based pattern:

- **`modules/`**: Reusable infrastructure components (VPC, EKS, Karpenter, etc.)
- **`environments/dev/`**: Full deployment (VPC + EKS + add-ons)
- **`environments/existing-cluster/`**: Add-ons only for existing EKS clusters ([guide](docs/existing-cluster.md))

Each module is self-contained with its own `main.tf`, `variables.tf`, and `outputs.tf`.

### Kubernetes Structure

The `kubernetes/` directory uses Kustomize with model-specific overlays:

- **`base/ray/`**: Core resources (RayJob, ConfigMap, PVCs)
- **`components/`**: Reusable training components (qlora, lora, fsdp) and settings (quick-test, full-training)
- **`overlays/<model>/`**: Model-specific overlays with **inline resource configuration**

Each model overlay includes resource config (GPU count, instance type, memory, CPU) directly inline, allowing easy customization:

```yaml
# overlays/llama-7b/kustomization.yaml
components:
  - ../../components/image
  - ../../components/training/qlora
  # - ../../components/settings/quick-test  # Optional

patches:
  # Resource configuration (inline - customize as needed)
  - target:
      kind: RayJob
      name: sft-training-ray
    patch: |-
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/replicas
        value: 1
      # ... GPU, memory, CPU, instance type patches

  # Model configuration
  - target:
      kind: ConfigMap
    patch: |-
      - op: replace
        path: /data/model_name
        value: "meta-llama/Llama-2-7b-hf"
```

## Setup Script

The `scripts/setup.sh` script provides a single entry point for all operations:

```bash
./scripts/setup.sh <command> [options]
```

### Commands

| Command | Description |
|---------|-------------|
| `deploy` | Deploy EKS cluster with Karpenter, KubeRay, EFS, and S3 |
| `destroy` | Destroy all infrastructure |
| `build` | Build Docker image locally |
| `build-push` | Build and push Docker image to ECR (creates repo if needed) |
| `configure` | Configure training and S3 storage (interactive or with arguments) |
| `configure-s3` | Configure S3 storage only (called automatically by `configure`) |
| `show-config` | Display current training configuration |
| `train` | Start training job |
| `stop` | Stop running training job |
| `hf-token` | Set up HuggingFace token for gated models |
| `check` | Verify prerequisites (aws, terraform, kubectl, docker) |

### Configuration Options

```bash
# Interactive configuration
./scripts/setup.sh configure

# Direct configuration (model has optimal defaults, settings optional)
./scripts/setup.sh configure <model> [settings]

# Examples
./scripts/setup.sh configure tinyllama-1b quick-test
./scripts/setup.sh configure llama-7b full-training
./scripts/setup.sh configure llama-70b
```

**Available models (with optimal defaults):**

| Model | GPUs | GPU Type | Training | Notes |
|-------|------|----------|----------|-------|
| `tinyllama-1b` | 1 | A10G (24GB) | qlora | Fast testing, no HF token |
| `llama-7b` | 1 | A10G (24GB) | qlora | Memory efficient |
| `llama-13b` | 4 | A10G (24GB) | lora+DDP | Multi-GPU |
| `llama-70b` | 8 | A10G (24GB) | fsdp | Multi-node |

**Settings:** `quick-test` (100 samples, 1 epoch) or `full-training` (full dataset, 3 epochs)

## Model Overlays

Each model overlay specifies GPU type (not instance type) for **Spot instance flexibility**:

| Overlay | GPUs | GPU Type | Training | Notes |
|---------|------|----------|----------|-------|
| `tinyllama-1b` | 1 | A10G (24GB) | qlora | Fast testing, no HF token |
| `llama-7b` | 1 | A10G (24GB) | qlora | Memory-efficient with 4-bit |
| `llama-13b` | 4 | A10G (24GB) | lora+DDP | Requires HF token |
| `llama-70b` | 8 | A10G (24GB) | fsdp | Multi-node, requires FSDP |

Overlays use `karpenter.k8s.aws/instance-gpu-name` (e.g., `a10g`, `l40s`) instead of specific instance types. This allows Karpenter to choose from all instances with that GPU, maximizing Spot availability while guaranteeing minimum VRAM.

### Customizing Overlays

Each overlay file (`kubernetes/overlays/<model>/kustomization.yaml`) contains all configuration directly in patches. Modify these sections for your requirements:

#### 1. Components

Select training method and settings:

```yaml
components:
  - ../../components/image                    # Required: container image
  - ../../components/training/qlora           # Training: qlora, lora, or fsdp
  - ../../components/scheduling/kueue         # Required: gang scheduling
  - ../../components/storage/s3               # Required: checkpoint storage
  - ../../components/settings/quick-test      # Settings: quick-test or full-training
```

| Component | Options | Description |
|-----------|---------|-------------|
| training | `qlora`, `lora`, `fsdp` | QLoRA (4-bit), LoRA (full), FSDP (sharded) |
| settings | `quick-test`, `full-training` | 100 samples/1 epoch vs full dataset/3 epochs |

#### 2. Pod Configuration (Kubernetes resources)

Controls how many pods and GPUs per pod:

```yaml
patches:
  - target:
      kind: RayJob
      name: sft-training-ray
    patch: |-
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/replicas
        value: 1                    # Number of worker pods
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/minReplicas
        value: 1
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/maxReplicas
        value: 1
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/rayStartParams/num-gpus
        value: "4"                  # GPUs visible to Ray per pod
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/template/spec/containers/0/resources/requests/nvidia.com~1gpu
        value: "4"                  # GPU requests per pod
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/template/spec/containers/0/resources/limits/nvidia.com~1gpu
        value: "4"
      - op: add
        path: /metadata/annotations/kueue.x-k8s.io~1pod-group-total-count
        value: "2"                  # Gang size: worker pods + 1 head
```

#### 3. Ray Train Worker Configuration (distributed training)

Controls how Ray Train spawns workers inside pods for DDP:

```yaml
  - target:
      kind: ConfigMap
      name: ray-training-config
    patch: |-
      - op: replace
        path: /data/num_workers
        value: "4"                  # Ray Train workers (typically = total GPUs)
      - op: replace
        path: /data/gpus_per_worker
        value: "1"                  # GPUs per Ray worker (typically 1)
      - op: replace
        path: /data/cpus_per_worker
        value: "10"                 # CPUs per Ray worker
```

#### 4. Instance Configuration (node resources)

Controls memory, CPU, and node selection:

```yaml
  - target:
      kind: RayJob
      name: sft-training-ray
    patch: |-
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/template/spec/containers/0/resources/requests/memory
        value: "160Gi"
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/template/spec/containers/0/resources/limits/memory
        value: "180Gi"
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/template/spec/containers/0/resources/requests/cpu
        value: "40"
      - op: replace
        path: /spec/rayClusterSpec/workerGroupSpecs/0/template/spec/containers/0/resources/limits/cpu
        value: "48"
      - op: add
        path: /spec/rayClusterSpec/workerGroupSpecs/0/template/spec/affinity
        value:
          nodeAffinity:
            requiredDuringSchedulingIgnoredDuringExecution:
              nodeSelectorTerms:
                - matchExpressions:
                    - key: node.kubernetes.io/instance-type
                      operator: In
                      values: ["g5.12xlarge"]
```

For flexible GPU type selection (recommended for Spot):

```yaml
                - matchExpressions:
                    - key: karpenter.k8s.aws/instance-gpu-name
                      operator: In
                      values: ["l40s"]           # GPU type
                    - key: karpenter.k8s.aws/instance-gpu-count
                      operator: Gt
                      values: ["3"]              # At least 4 GPUs
```

| GPU Name | VRAM | Instance Family | Use Case |
|----------|------|-----------------|----------|
| `a10g` | 24GB | g5.* | Cost-effective training |
| `l40s` | 48GB | g6e.* | Larger models |
| RTX 6000 Blackwell | 96GB | g7e.* | Very large models (235B+) |
| `a100` | 40GB | p4d.* | High-performance training (Capacity Block) |
| `h100` | 80GB | p5.* | Largest models (Capacity Block) |

#### 5. Model Configuration (ConfigMap)

Configure model, dataset, and training hyperparameters:

```yaml
  - target:
      kind: ConfigMap
      name: ray-training-config
    patch: |-
      - op: replace
        path: /data/model_name
        value: "meta-llama/Llama-2-7b-hf"
      - op: replace
        path: /data/dataset_name
        value: "tatsu-lab/alpaca"
      - op: replace
        path: /data/max_samples
        value: "0"                  # 0 = full dataset
      - op: replace
        path: /data/batch_size
        value: "2"
      - op: replace
        path: /data/gradient_accumulation_steps
        value: "8"
      - op: replace
        path: /data/learning_rate
        value: "1e-4"
      - op: replace
        path: /data/max_seq_length
        value: "2048"
      - op: replace
        path: /data/lora_r
        value: "16"
      - op: replace
        path: /data/lora_alpha
        value: "32"

```

#### Common Topology Patterns

| Scenario | replicas | num-gpus | GPU req | num_workers | gpus_per_worker | gang |
|----------|----------|----------|---------|-------------|-----------------|------|
| 1 pod, 1 GPU | 1 | "1" | "1" | 1 | 1 | 2 |
| 1 pod, 2 GPUs | 1 | "2" | "2" | 2 | 1 | 2 |
| 1 pod, 4 GPUs | 1 | "4" | "4" | 4 | 1 | 2 |
| 1 pod, 8 GPUs | 1 | "8" | "8" | 8 | 1 | 2 |
| 2 pods, 1 GPU each | 2 | "1" | "1" | 2 | 1 | 3 |
| 4 pods, 1 GPU each | 4 | "1" | "1" | 4 | 1 | 5 |

**Key relationships:**
- `num_workers` = total GPUs across all pods
- `gpus_per_worker` = typically 1 (one Ray worker per GPU)
- `gang` = `replicas` + 1 (worker pods + head pod)
- For single-pod: `replicas=1`, `num-gpus=N`, `num_workers=N`
- For multi-pod: `replicas=N`, `num-gpus=1`, `num_workers=N`

### Quick Start Examples

```bash
# Testing/Development - TinyLlama with QLoRA
./scripts/setup.sh configure tinyllama-1b quick-test
./scripts/setup.sh train

# Production - Llama-7B full training
./scripts/setup.sh configure llama-7b full-training
./scripts/setup.sh train

# Large Model - Llama-70B distributed across 2 nodes
./scripts/setup.sh configure llama-70b full-training
./scripts/setup.sh train

```

## Capacity Blocks for p-Family Instances (A100/H100)

AWS `p`-family GPU instances (A100, H100) are not available through standard Spot or on-demand pools. They require **Capacity Blocks for ML** -- pre-reserved compute blocks purchased through the AWS console.

### How It Works

The project deploys a dedicated `gpu-capacity-block` NodePool and EC2NodeClass alongside the standard `gpu-training` NodePool:

| NodePool | Capacity Type | Instance Category | Use Case |
|----------|--------------|-------------------|----------|
| `gpu-training` | spot, on-demand | g, p | g-family (A10G, L40S) |
| `gpu-capacity-block` | reserved | p | p-family (A100, H100) via Capacity Blocks |

The `gpu-capacity-block` EC2NodeClass uses `capacityReservationSelectorTerms` to match your active Capacity Block reservations by tag (default: `purpose: ml-training`).

### Setup

1. **Purchase a Capacity Block** in the AWS console (EC2 > Capacity Reservations > Purchase Capacity Block).
   **Important:** Tags must be specified at purchase time -- Capacity Block reservations cannot be tagged after creation. Ensure you add the `purpose: ml-training` tag (or your custom tag matching `capacity_block_tags`) during the purchase flow.
2. **Deploy infrastructure** -- the `gpu-capacity-block` NodePool is enabled by default:
   ```bash
   ./scripts/setup.sh deploy
   ```

To customize the reservation tag:
```hcl
# terraform/environments/dev/terraform.tfvars
capacity_block_tags = { "purpose" = "my-custom-tag" }
```

To disable the Capacity Block NodePool:
```hcl
# terraform/environments/dev/terraform.tfvars
enable_capacity_block_nodepool = false
```

### Using p-Family Instances in Overlays

Overlays targeting p-family instances should add a `nodeSelector` for the `gpu-capacity-block` NodePool and set the capacity type to `reserved`:

```yaml
# In your overlay's kustomization.yaml patches
- op: add
  path: /spec/rayClusterSpec/workerGroupSpecs/0/template/spec/nodeSelector
  value:
    karpenter.sh/nodepool: gpu-capacity-block
    karpenter.sh/capacity-type: reserved
    karpenter.k8s.aws/instance-gpu-name: a100   # or h100
```

### Verification

```bash
# Verify NodePool and EC2NodeClass exist
kubectl get nodepools gpu-capacity-block
kubectl get ec2nodeclasses gpu-capacity-block

# Confirm reserved capacity type
kubectl get nodepool gpu-capacity-block -o yaml | grep -A2 capacity-type

# Check Capacity Block reservations in AWS
aws ec2 describe-capacity-reservations --filters Name=tag:purpose,Values=ml-training
```

## Using with Existing EKS Clusters

If you already have an EKS cluster, you can deploy only the training add-ons (Karpenter, KubeRay, Kueue, EFS, S3) without creating a new VPC or cluster.

```bash
# Switch to existing-cluster environment
./scripts/setup.sh set-env existing-cluster

# Configure and deploy
cd terraform/environments/existing-cluster
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your cluster details
./scripts/setup.sh deploy
```

See the [Existing Cluster Guide](docs/existing-cluster.md) for detailed setup instructions.

## Prerequisites

- AWS CLI configured with appropriate credentials
- Terraform >= 1.5.0
- kubectl
- Docker
- (Optional) HuggingFace token for gated models (Llama)
  - Get token at: https://huggingface.co/settings/tokens
  - Set up via: `./scripts/setup.sh hf-token`

## Quick Start

```bash
# 1. Check prerequisites
./scripts/setup.sh check

# 2. Deploy infrastructure (~15-20 minutes)
./scripts/setup.sh deploy

# 3. Build and push training container
./scripts/setup.sh build-push

# 4. Configure training (also configures S3 storage automatically)
./scripts/setup.sh configure tinyllama-1b quick-test

# 5. Start training
./scripts/setup.sh train

# 6. Monitor
kubectl get rayjob -n ml-training -w
kubectl logs -f -n ml-training -l ray.io/node-type=head

# 7. Stop training (optional)
./scripts/setup.sh stop

# 8. Check outputs in S3
aws s3 ls s3://<bucket-name>/outputs/ --recursive

# 9. Destroy infrastructure when done
./scripts/setup.sh destroy
```

## License

MIT
