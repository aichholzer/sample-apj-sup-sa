################################################################################
# GPU Node Group Module for EKS
################################################################################

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for the node group"
  type        = list(string)
}

variable "instance_types" {
  description = "GPU instance types"
  type        = list(string)
  default     = ["g5.xlarge"]
}

variable "min_size" {
  description = "Minimum number of nodes"
  type        = number
  default     = 0
}

variable "max_size" {
  description = "Maximum number of nodes"
  type        = number
  default     = 4
}

variable "desired_size" {
  description = "Desired number of nodes"
  type        = number
  default     = 1
}

variable "disk_size" {
  description = "Disk size in GB"
  type        = number
  default     = 200
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

# Get EKS cluster info
data "aws_eks_cluster" "cluster" {
  name = var.cluster_name
}

# IAM role for GPU nodes
resource "aws_iam_role" "gpu_node" {
  name = "${var.cluster_name}-gpu-node-role"

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

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "gpu_node_policies" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  ])

  policy_arn = each.value
  role       = aws_iam_role.gpu_node.name
}

# Launch template for GPU nodes
resource "aws_launch_template" "gpu" {
  name_prefix = "${var.cluster_name}-gpu-"

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.disk_size
      volume_type           = "gp3"
      delete_on_termination = true
      encrypted             = true
    }
  }

  # Use EKS-optimized AMI with GPU support
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  tag_specifications {
    resource_type = "instance"
    tags = merge(var.tags, {
      Name = "${var.cluster_name}-gpu-node"
    })
  }

  tags = var.tags
}

# GPU node group
resource "aws_eks_node_group" "gpu" {
  cluster_name    = var.cluster_name
  node_group_name = "${var.cluster_name}-gpu"
  node_role_arn   = aws_iam_role.gpu_node.arn
  subnet_ids      = var.subnet_ids

  instance_types = var.instance_types
  ami_type       = "AL2_x86_64_GPU"
  capacity_type  = "ON_DEMAND"

  scaling_config {
    min_size     = var.min_size
    max_size     = var.max_size
    desired_size = var.desired_size
  }

  launch_template {
    id      = aws_launch_template.gpu.id
    version = aws_launch_template.gpu.latest_version
  }

  labels = {
    "node.kubernetes.io/instance-type" = var.instance_types[0]
    "nvidia.com/gpu"                   = "true"
    "workload"                         = "gpu-training"
  }

  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  update_config {
    max_unavailable = 1
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.gpu_node_policies
  ]
}

output "node_group_name" {
  description = "GPU node group name"
  value       = aws_eks_node_group.gpu.node_group_name
}

output "node_group_arn" {
  description = "GPU node group ARN"
  value       = aws_eks_node_group.gpu.arn
}
