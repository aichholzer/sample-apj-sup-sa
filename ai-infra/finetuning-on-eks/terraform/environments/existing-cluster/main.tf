################################################################################
# Existing Cluster Environment - Fine-tuning on EKS
################################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
    kubectl = {
      source  = "alekc/kubectl"
      version = "~> 2.0"
    }
  }

  # Uncomment and configure for remote state
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "fine-tuning-on-eks/existing-cluster/terraform.tfstate"
  #   region         = "us-west-2"
  #   encrypt        = true
  #   dynamodb_table = "terraform-lock"
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "fine-tuning-on-eks"
      Environment = "existing-cluster"
      ManagedBy   = "terraform"
    }
  }
}

provider "kubernetes" {
  host                   = data.aws_eks_cluster.this.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", var.cluster_name]
  }
}

provider "helm" {
  kubernetes {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)

    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", var.cluster_name]
    }
  }
}

provider "kubectl" {
  host                   = data.aws_eks_cluster.this.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
  load_config_file       = false

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", var.cluster_name]
  }
}

################################################################################
# Karpenter Node IAM Role
################################################################################

resource "aws_iam_role" "karpenter_node" {
  count = var.enable_karpenter ? 1 : 0

  name = "${var.cluster_name}-karpenter-node"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Environment = "existing-cluster"
  }
}

resource "aws_iam_role_policy_attachment" "karpenter_node_policies" {
  for_each = var.enable_karpenter ? toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  ]) : toset([])

  policy_arn = each.value
  role       = aws_iam_role.karpenter_node[0].name
}

################################################################################
# Karpenter
################################################################################

module "karpenter" {
  source = "../../modules/karpenter"
  count  = var.enable_karpenter ? 1 : 0

  cluster_name                       = var.cluster_name
  cluster_endpoint                   = data.aws_eks_cluster.this.endpoint
  cluster_certificate_authority_data = data.aws_eks_cluster.this.certificate_authority[0].data
  oidc_provider_arn                  = data.aws_iam_openid_connect_provider.this.arn
  node_iam_role_arn                  = aws_iam_role.karpenter_node[0].arn

  # Subnet and security group configuration
  subnet_ids         = var.private_subnet_ids
  security_group_ids = var.cluster_security_group_ids

  # GPU NodePool configuration
  enable_gpu_nodepool     = true
  gpu_instance_categories = var.gpu_instance_categories

  # Enable spot termination handling
  enable_spot_termination = true

  # Capacity Block for p-family instances (A100/H100)
  enable_capacity_block_nodepool = var.enable_capacity_block_nodepool
  capacity_block_tags            = var.capacity_block_tags

  tags = {
    Environment = "existing-cluster"
  }

  depends_on = [aws_iam_role_policy_attachment.karpenter_node_policies]
}

################################################################################
# EFS for Shared ML Data (Multi-Node Training)
################################################################################

module "efs" {
  source = "../../modules/efs"
  count  = var.enable_efs ? 1 : 0

  cluster_name       = var.cluster_name
  vpc_id             = var.vpc_id
  vpc_cidr           = var.vpc_cidr
  private_subnet_ids = var.private_subnet_ids
  oidc_provider_arn  = data.aws_iam_openid_connect_provider.this.arn

  tags = {
    Environment = "existing-cluster"
  }
}

################################################################################
# S3 Storage for Checkpoints and Model Outputs
################################################################################

module "s3" {
  source = "../../modules/s3"
  count  = var.enable_s3 ? 1 : 0

  cluster_name         = var.cluster_name
  oidc_provider_arn    = data.aws_iam_openid_connect_provider.this.arn
  namespace            = "ml-training"
  service_account_name = "ray-training-sa"

  tags = {
    Environment = "existing-cluster"
  }
}

################################################################################
# KubeRay Operator (Multi-Node Training)
################################################################################

module "kuberay" {
  source = "../../modules/kuberay"
  count  = var.enable_kuberay ? 1 : 0

  cluster_name    = var.cluster_name
  kuberay_version = "1.2.2"

  tags = {
    Environment = "existing-cluster"
  }
}

################################################################################
# Kueue (Gang Scheduling)
################################################################################

module "kueue" {
  source = "../../modules/kueue"
  count  = var.enable_kueue ? 1 : 0

  cluster_name  = var.cluster_name
  kueue_version = "0.16.0"

  tags = {
    Environment = "existing-cluster"
  }
}

################################################################################
# Cilium CNI (Chaining Mode)
################################################################################

module "cilium" {
  source = "../../modules/cilium"
  count  = var.enable_cilium ? 1 : 0

  cluster_name                       = var.cluster_name
  cluster_endpoint                   = data.aws_eks_cluster.this.endpoint
  cluster_certificate_authority_data = data.aws_eks_cluster.this.certificate_authority[0].data

  enable_hubble    = true
  enable_hubble_ui = true

  tags = {
    Environment = "existing-cluster"
  }
}

################################################################################
# NVIDIA GPU Operator
################################################################################

resource "helm_release" "gpu_operator" {
  count = var.enable_gpu_operator ? 1 : 0

  name             = "gpu-operator"
  repository       = "https://helm.ngc.nvidia.com/nvidia"
  chart            = "gpu-operator"
  namespace        = "gpu-operator"
  create_namespace = true
  version          = "v24.9.2"

  # Disable driver and toolkit (pre-installed in AMI)
  set {
    name  = "driver.enabled"
    value = "false"
  }
  set {
    name  = "toolkit.enabled"
    value = "false"
  }

  # Tolerate GPU node taints
  set {
    name  = "daemonsets.tolerations[0].key"
    value = "nvidia.com/gpu"
  }
  set {
    name  = "daemonsets.tolerations[0].operator"
    value = "Exists"
  }
  set {
    name  = "daemonsets.tolerations[0].effect"
    value = "NoSchedule"
  }
}
