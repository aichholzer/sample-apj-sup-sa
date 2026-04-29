# Terraform Infrastructure

This directory contains Terraform configurations for deploying EKS clusters with GPU support, Cilium CNI, and Karpenter for GenAI workloads.

## Directory Structure

```
terraform/
├── modules/                    # Reusable infrastructure components
│   ├── vpc/                    # VPC and networking
│   ├── eks/                    # EKS cluster
│   ├── gpu-nodegroup/          # Managed GPU node group (legacy)
│   ├── cilium/                 # Cilium CNI (chaining mode)
│   └── karpenter/              # Karpenter node provisioner
│
└── environments/               # Environment-specific deployments
    └── dev/                    # Development environment
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                            AWS Account                               │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                         VPC Module                           │    │
│  │  • Public subnets (NAT Gateway, Load Balancers)             │    │
│  │  • Private subnets (EKS nodes, pods)                        │    │
│  │  • Karpenter discovery tags                                  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                │                                     │
│  ┌─────────────────────────────▼───────────────────────────────┐    │
│  │                        EKS Module                            │    │
│  │  • Control plane (Kubernetes API)                           │    │
│  │  • System node group (m5.large)                             │    │
│  │  • IRSA (IAM Roles for Service Accounts)                    │    │
│  │  • Add-ons: CoreDNS, kube-proxy, VPC CNI, EBS CSI           │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                │                                     │
│         ┌──────────────────────┼──────────────────────┐             │
│         ▼                      ▼                      ▼             │
│  ┌─────────────┐      ┌──────────────┐      ┌──────────────┐       │
│  │   Cilium    │      │  Karpenter   │      │    NVIDIA    │       │
│  │   Module    │      │    Module    │      │ Device Plugin│       │
│  │             │      │              │      │              │       │
│  │ • Chaining  │      │ • NodePools  │      │ • GPU expose │       │
│  │   mode      │      │ • EC2Node-   │      │ • Scheduling │       │
│  │ • Hubble    │      │   Classes    │      │              │       │
│  │ • Policies  │      │ • Spot       │      │              │       │
│  └─────────────┘      └──────────────┘      └──────────────┘       │
└─────────────────────────────────────────────────────────────────────┘
```

## Modules

### vpc

Creates the network foundation for EKS.

| Resource | Description |
|----------|-------------|
| VPC | 10.0.0.0/16 CIDR block |
| Public Subnets | For NAT Gateway, ALB/NLB |
| Private Subnets | For EKS nodes and pods |
| NAT Gateway | Single NAT for cost optimization |
| Subnet Tags | Karpenter discovery, ELB integration |

**Key Variables:**
- `name` - Name prefix for resources
- `cidr` - VPC CIDR block
- `azs` - Availability zones
- `cluster_name` - For Karpenter subnet discovery tags

### eks

Creates the EKS cluster with system node group.

| Resource | Description |
|----------|-------------|
| EKS Cluster | Kubernetes control plane |
| System Node Group | m5.large nodes for system workloads |
| OIDC Provider | For IRSA (pod IAM roles) |
| Add-ons | CoreDNS, kube-proxy, VPC CNI, EBS CSI |

**Key Variables:**
- `name` - Cluster name
- `cluster_version` - Kubernetes version (default: 1.33)
- `vpc_id` - VPC to deploy into
- `subnet_ids` - Private subnets for nodes

**Key Outputs:**
- `cluster_name`, `cluster_endpoint` - For kubectl configuration
- `oidc_provider_arn` - For IRSA roles
- `cluster_primary_security_group_id` - For Karpenter nodes

### cilium

Deploys Cilium CNI in chaining mode alongside AWS VPC CNI.

| Feature | Description |
|---------|-------------|
| CNI Chaining | AWS VPC CNI handles IPAM, Cilium handles policies |
| Hubble | Network flow observability |
| Native Routing | No overlay, direct VPC routing |
| Network Policies | L3/L4/L7 policy enforcement |

**Key Variables:**
- `cluster_name` - EKS cluster name
- `enable_hubble` - Enable Hubble observability (default: true)
- `enable_hubble_ui` - Enable Hubble UI (default: true)
- `cilium_version` - Helm chart version

**Why Chaining Mode?**
- Preserves AWS VPC CNI benefits (native VPC IPs, security groups)
- Adds Cilium's advanced network policies and observability
- No performance overhead from overlay networks

### karpenter

Deploys Karpenter for dynamic node provisioning.

| Resource | Description |
|----------|-------------|
| IAM Role | Controller permissions (EC2, IAM, Pricing) |
| SQS Queue | Spot interruption handling |
| EventBridge Rules | Instance state change notifications |
| NodePool (default) | CPU instances for general workloads |
| NodePool (gpu) | GPU instances for training workloads |
| EC2NodeClass | AMI, subnets, security groups config |

**Key Variables:**
- `cluster_name` - EKS cluster name
- `oidc_provider_arn` - For IRSA
- `node_iam_role_arn` - IAM role for provisioned nodes
- `subnet_ids` - Subnets for node placement
- `gpu_instance_types` - Allowed GPU instance types
- `enable_gpu_nodepool` - Create GPU NodePool (default: true)

**NodePool Configuration:**

| NodePool | Instance Types | Capacity | Use Case |
|----------|---------------|----------|----------|
| default | m5, m6i (large-2xlarge) | spot, on-demand | General workloads |
| gpu-training | g5 (xlarge-12xlarge) | on-demand | GPU training jobs |

### gpu-nodegroup (Legacy)

Managed node group for GPU instances. Used when Karpenter is disabled.

| Resource | Description |
|----------|-------------|
| IAM Role | Node permissions |
| Launch Template | EBS, metadata config |
| EKS Node Group | GPU AMI, taints, labels |

**Note:** This module is disabled by default when Karpenter is enabled.

## Environments

### dev

Development environment with cost-optimized settings.

**Configuration:**

```hcl
# Key settings in terraform.tfvars
region = "us-west-2"
name   = "genai-eks-dev"

gpu_instance_types = ["g5.xlarge", "g5.2xlarge", "g5.4xlarge", "g5.12xlarge"]

enable_cilium    = true   # Cilium CNI with Hubble
enable_karpenter = true   # Dynamic node provisioning
```

**Features:**
- Single NAT Gateway (cost savings)
- Karpenter with scale-to-zero GPU nodes
- Hubble UI for debugging
- Spot instances for CPU workloads
- Automatic kubeconfig update (see below)

**Deploy:**

```bash
cd terraform/environments/dev
terraform init
terraform apply
```

### Automatic kubeconfig Update

The dev environment uses `terraform_data` to automatically update your local kubeconfig after the EKS cluster is created. This solves the "chicken-and-egg" authentication problem where Helm/kubectl providers need cluster credentials that don't exist until the cluster is created.

```hcl
resource "terraform_data" "update_kubeconfig" {
  triggers_replace = [module.eks.cluster_endpoint]

  provisioner "local-exec" {
    command = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
  }

  depends_on = [module.eks]
}
```

**How it works:**

1. EKS cluster is created
2. `terraform_data` runs `aws eks update-kubeconfig` locally
3. Your `~/.kube/config` is updated with cluster credentials
4. Helm releases (Cilium, Karpenter, NVIDIA plugin) can now authenticate
5. All completes in a single `terraform apply`

**Why `terraform_data` instead of `null_resource`?**

| Aspect | null_resource | terraform_data |
|--------|---------------|----------------|
| Provider | Requires hashicorp/null | Built-in (Terraform 1.4+) |
| Purpose | Legacy workaround | Designed for lifecycle management |
| Recommended | No (maintained but legacy) | Yes |

**Note:** This modifies your local `~/.kube/config` as a side effect. If you prefer manual control, remove this resource and run kubeconfig update separately:

```bash
terraform apply -target=module.vpc -target=module.eks
aws eks update-kubeconfig --region us-west-2 --name genai-eks-dev
terraform apply
```

## Creating a Production Environment

### Step 1: Create Directory Structure

```bash
mkdir -p terraform/environments/prod
```

### Step 2: Create main.tf

Copy and modify from dev:

```bash
cp terraform/environments/dev/main.tf terraform/environments/prod/main.tf
```

Key changes for production:

```hcl
# terraform/environments/prod/main.tf

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "fine-tuning-on-eks"
      Environment = "prod"          # Changed
      ManagedBy   = "terraform"
    }
  }
}

# Update module tags
tags = {
  Environment = "prod"              # Changed
}
```

### Step 3: Create terraform.tfvars

```hcl
# terraform/environments/prod/terraform.tfvars

region = "us-west-2"
name   = "genai-eks-prod"           # Different name

# GPU instances - larger for production workloads
gpu_instance_types = ["g5.4xlarge", "g5.8xlarge", "g5.12xlarge", "g5.48xlarge"]

enable_cilium    = true
enable_karpenter = true
```

### Step 4: Production-Specific Modifications

#### 1. High Availability VPC

Modify the VPC module call for multi-NAT:

```hcl
# In modules/vpc/main.tf, add variable:
variable "single_nat_gateway" {
  description = "Use single NAT gateway (cost savings) or one per AZ (HA)"
  type        = bool
  default     = true
}

# In VPC module:
single_nat_gateway = var.single_nat_gateway

# In prod main.tf:
module "vpc" {
  source = "../../modules/vpc"
  # ...
  single_nat_gateway = false  # One NAT per AZ for HA
}
```

#### 2. Larger System Node Group

Modify EKS module or override in prod:

```hcl
# Consider modifying eks module to accept node group config
# Or create prod-specific node group settings

eks_managed_node_groups = {
  system = {
    instance_types = ["m5.xlarge"]  # Larger instances
    min_size       = 2
    max_size       = 5
    desired_size   = 3              # More nodes
  }
}
```

#### 3. Stricter Karpenter Limits

```hcl
# In prod, you may want higher limits for GPU NodePool
variable "gpu_nodepool_cpu_limit" {
  default = 2000  # Higher for prod
}

variable "gpu_nodepool_memory_limit" {
  default = "4000Gi"  # Higher for prod
}
```

#### 4. Remote State Backend

```hcl
# Uncomment and configure in prod main.tf:
terraform {
  backend "s3" {
    bucket         = "your-terraform-state-bucket"
    key            = "fine-tuning-on-eks/prod/terraform.tfstate"
    region         = "us-west-2"
    encrypt        = true
    dynamodb_table = "terraform-lock"
  }
}
```

### Step 5: Deploy Production

```bash
cd terraform/environments/prod
terraform init
terraform plan    # Review changes carefully
terraform apply
```

## Environment Comparison

| Feature | Dev | Prod (Recommended) |
|---------|-----|-------------------|
| NAT Gateway | Single | One per AZ |
| System Nodes | 2x m5.large | 3x m5.xlarge |
| GPU Instances | g5.xlarge - g5.12xlarge | g5.4xlarge - g5.48xlarge |
| Spot for CPU | Yes | Optional |
| State Backend | Local | S3 + DynamoDB |
| Karpenter Limits | Lower | Higher |

## Common Operations

### Update kubeconfig

```bash
aws eks update-kubeconfig --region us-west-2 --name <cluster-name>
```

### Scale GPU Nodes (Karpenter)

GPU nodes scale automatically based on pending pods. To manually test:

```bash
# Create a GPU pod - Karpenter will provision a node
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: gpu-test
spec:
  containers:
  - name: cuda
    image: nvidia/cuda:12.1.0-base-ubuntu22.04
    command: ["nvidia-smi"]
    resources:
      limits:
        nvidia.com/gpu: 1
  tolerations:
  - key: nvidia.com/gpu
    operator: Exists
    effect: NoSchedule
  restartPolicy: Never
EOF
```

### Access Hubble UI

```bash
kubectl port-forward -n kube-system svc/hubble-ui 8080:80
# Open http://localhost:8080
```

### Destroy Environment

```bash
cd terraform/environments/<env>
terraform destroy
```

## Troubleshooting

### Karpenter Not Provisioning Nodes

1. Check Karpenter logs:
   ```bash
   kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter
   ```

2. Verify NodePool and EC2NodeClass:
   ```bash
   kubectl get nodepools
   kubectl get ec2nodeclasses
   ```

3. Check pod events:
   ```bash
   kubectl describe pod <pod-name>
   ```

### Cilium/Hubble Issues

1. Check Cilium status:
   ```bash
   kubectl exec -n kube-system ds/cilium -c cilium-agent -- cilium status
   ```

2. Verify chaining mode:
   ```bash
   kubectl exec -n kube-system ds/cilium -c cilium-agent -- cilium config | grep chain
   ```

### EKS Upgrade Path

EKS only allows upgrading one minor version at a time:

```
1.29 → 1.30 → 1.31 → 1.32 → 1.33
```

Update `cluster_version` in your environment and run `terraform apply` for each step.
