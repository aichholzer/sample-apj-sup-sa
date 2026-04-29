################################################################################
# Variables - Existing EKS Cluster Environment
################################################################################

variable "region" {
  description = "AWS region where the existing EKS cluster is located"
  type        = string
}

variable "cluster_name" {
  description = "Name of the existing EKS cluster"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where the existing EKS cluster is running"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC (for EFS security group rules)"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs where worker nodes will be provisioned"
  type        = list(string)
}

variable "cluster_security_group_ids" {
  description = "List of security group IDs to attach to Karpenter nodes (typically cluster security group and node security group)"
  type        = list(string)
}

################################################################################
# Component Toggles
################################################################################

variable "enable_karpenter" {
  description = "Enable Karpenter for GPU node provisioning"
  type        = bool
  default     = true
}

variable "enable_kuberay" {
  description = "Enable KubeRay operator for distributed training"
  type        = bool
  default     = true
}

variable "enable_kueue" {
  description = "Enable Kueue for gang scheduling and job queuing"
  type        = bool
  default     = true
}

variable "enable_efs" {
  description = "Enable EFS for shared ML data storage (creates new EFS)"
  type        = bool
  default     = true
}

variable "enable_s3" {
  description = "Enable S3 for checkpoints and model outputs"
  type        = bool
  default     = true
}

variable "enable_gpu_operator" {
  description = "Enable NVIDIA GPU Operator (includes device plugin and DCGM exporter)"
  type        = bool
  default     = true
}

variable "enable_cilium" {
  description = "Enable Cilium CNI (most existing clusters already have a CNI)"
  type        = bool
  default     = false
}

variable "enable_capacity_block_nodepool" {
  description = "Enable Capacity Block NodePool for p-family instances (A100/H100)"
  type        = bool
  default     = false
}

################################################################################
# Optional Overrides
################################################################################

variable "existing_efs_file_system_id" {
  description = "Use an existing EFS file system instead of creating a new one (leave empty to create new)"
  type        = string
  default     = ""
}

variable "gpu_instance_categories" {
  description = "GPU instance categories for Karpenter (g = A10G/L40S/RTX, p = A100/H100)"
  type        = list(string)
  default     = ["g"]
}

variable "capacity_block_tags" {
  description = "Tags to match Capacity Block reservations (only used if enable_capacity_block_nodepool = true)"
  type        = map(string)
  default     = { "purpose" = "ml-training" }
}
