# Using Fine-tuning on EKS with Existing Clusters

This guide shows how to deploy only the training add-ons (Karpenter, KubeRay, Kueue, EFS, S3) to an existing EKS cluster without creating new VPC or EKS resources.

## Prerequisites

- **Existing EKS cluster** (Kubernetes 1.28+) with OIDC provider enabled
- **VPC details**: VPC ID, VPC CIDR, private subnet IDs, security group IDs
- **kubectl** configured for your cluster
- **AWS CLI** with credentials configured
- **Terraform** >= 1.5.0

## Quick Start

```bash
# 1. Switch to existing-cluster environment
./scripts/setup.sh set-env existing-cluster

# 2. Configure Terraform variables
cd terraform/environments/existing-cluster
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your cluster details

# 3. Deploy add-ons
./scripts/setup.sh deploy

# 4. Build and push training image
./scripts/setup.sh build-push

# 5. Configure and train
./scripts/setup.sh configure tinyllama-1b quick-test
./scripts/setup.sh train
```

## Finding Your Cluster Details

Use these AWS CLI commands to gather the required information:

### Cluster Name
```bash
aws eks list-clusters
```

### VPC Configuration
```bash
# VPC ID and subnets
aws eks describe-cluster --name <cluster> --query 'cluster.resourcesVpcConfig'

# VPC CIDR
aws ec2 describe-vpcs --vpc-ids <vpc-id> --query 'Vpcs[0].CidrBlock'

# Private subnets
aws ec2 describe-subnets --filters "Name=vpc-id,Values=<vpc-id>" "Name=tag:Name,Values=*private*" --query 'Subnets[*].SubnetId'
```

### Security Groups
```bash
# Cluster security groups
aws eks describe-cluster --name <cluster> --query 'cluster.resourcesVpcConfig.securityGroupIds'

# Cluster primary security group
aws eks describe-cluster --name <cluster> --query 'cluster.resourcesVpcConfig.clusterSecurityGroupId'
```

### OIDC Provider
```bash
# Verify OIDC provider exists
aws eks describe-cluster --name <cluster> --query 'cluster.identity.oidc.issuer'
```

## Component Toggles

The `existing-cluster` environment provides toggles for each component. Use these to disable components that are already installed in your cluster or that you manage separately:

| Toggle | Default | Disable When |
|--------|---------|-------------|
| `enable_karpenter` | `true` | You have your own node provisioner |
| `enable_kuberay` | `true` | KubeRay is already installed |
| `enable_kueue` | `true` | Kueue is already installed |
| `enable_efs` | `true` | You have existing shared storage |
| `enable_s3` | `true` | You want to manage S3 separately |
| `enable_gpu_operator` | `true` | Already installed in your cluster |
| `enable_cilium` | `false` | Most clusters already have a CNI |
| `enable_capacity_block_nodepool` | `false` | Only for p-family (A100/H100) |

### Example: Disable Existing Components

If your cluster already has Kueue and an EFS filesystem:

```hcl
# terraform/environments/existing-cluster/terraform.tfvars
enable_kueue = false
enable_efs = false
existing_efs_file_system_id = "fs-0123456789abcdef0"
```

## Using with Existing EFS

If you already have an EFS filesystem for shared storage:

```hcl
# terraform/environments/existing-cluster/terraform.tfvars
enable_efs = false
existing_efs_file_system_id = "fs-0123456789abcdef0"
```

The training configuration will automatically use the existing EFS filesystem for HuggingFace model cache.

## Using with Existing Karpenter

If your cluster already has Karpenter installed:

```hcl
# terraform/environments/existing-cluster/terraform.tfvars
enable_karpenter = false
```

**Note:** You'll need to ensure your existing Karpenter has NodePools configured to provision GPU instances. The project requires:

- A default NodePool for CPU-only workloads (Ray head pods)
- A GPU NodePool with `capacity-type: [spot, on-demand]` for g-family instances
- (Optional) A Capacity Block NodePool with `capacity-type: reserved` for p-family instances

Refer to the Karpenter module (`terraform/modules/karpenter/`) for NodePool configuration examples.

## Switching Between Environments

The project supports multiple Terraform environments. Switch between them using:

```bash
# Use existing-cluster environment
./scripts/setup.sh set-env existing-cluster

# Use dev environment (creates everything from scratch)
./scripts/setup.sh set-env dev
```

Or set via environment variable:

```bash
export FT_TF_ENV=existing-cluster
./scripts/setup.sh deploy
```

The selected environment is persisted in `.terraform-env` in the project root.

## Configuration File Structure

The `existing-cluster` environment has a minimal configuration:

```
terraform/environments/existing-cluster/
├── main.tf                 # Module composition (add-ons only)
├── variables.tf            # Variable declarations
├── outputs.tf              # Output definitions
├── terraform.tfvars.example # Example configuration
└── README.md               # Environment-specific docs
```

Key differences from the `dev` environment:
- No VPC module (uses existing VPC)
- No EKS module (uses existing cluster)
- All add-on modules are togglable via `enable_*` variables

## Next Steps

After deploying the add-ons:

1. **Build training image**: `./scripts/setup.sh build-push`
2. **Configure training**: `./scripts/setup.sh configure <model> [settings]`
3. **Start training**: `./scripts/setup.sh train`
4. **Monitor**: `kubectl get rayjob -n ml-training -w`

For detailed training configuration, see the main [README.md](../README.md).

## Troubleshooting

### OIDC Provider Not Found

If you see OIDC provider errors, ensure it's enabled on your cluster:

```bash
# Check if OIDC provider exists
aws eks describe-cluster --name <cluster> --query 'cluster.identity.oidc.issuer'

# Enable OIDC provider (if not enabled)
eksctl utils associate-iam-oidc-provider --cluster <cluster> --approve
```

### NodePool Not Provisioning Nodes

If Karpenter doesn't provision GPU nodes:

1. Check NodePool configuration: `kubectl get nodepools`
2. Verify instance types are available in your region
3. Check Karpenter logs: `kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter`

### EFS Mount Issues

If pods fail to mount EFS:

1. Verify security groups allow NFS traffic (port 2049) from cluster to EFS
2. Check EFS mount targets exist in your private subnets
3. Verify EFS CSI driver is installed: `kubectl get pods -n kube-system -l app=efs-csi-controller`
