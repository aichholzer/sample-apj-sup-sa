################################################################################
# Karpenter NodePool and EC2NodeClass Resources
################################################################################
#
# EC2NodeClass: Defines the EC2 configuration (AMI, subnets, security groups)
# NodePool: Defines scheduling constraints (instance types, taints, limits)
################################################################################

variable "subnet_ids" {
  description = "Subnet IDs for Karpenter to launch nodes in"
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group IDs for Karpenter nodes"
  type        = list(string)
}

variable "enable_gpu_nodepool" {
  description = "Enable GPU NodePool for training workloads"
  type        = bool
  default     = true
}

variable "gpu_instance_categories" {
  description = "GPU instance categories for training (g = A10G/L40S, p = A100/H100)"
  type        = list(string)
  default     = ["g", "p"]
}

variable "cpu_instance_types" {
  description = "CPU instance types for general workloads"
  type        = list(string)
  default     = ["m5.large", "m5.xlarge", "m5.2xlarge", "m6i.large", "m6i.xlarge", "m6i.2xlarge"]
}

variable "gpu_nodepool_cpu_limit" {
  description = "Total CPU limit for GPU NodePool"
  type        = number
  default     = 2000
}

variable "gpu_nodepool_memory_limit" {
  description = "Total memory limit (Gi) for GPU NodePool"
  type        = string
  default     = "8000Gi"
}

variable "cpu_nodepool_cpu_limit" {
  description = "Total CPU limit for CPU NodePool"
  type        = number
  default     = 100
}

variable "cpu_nodepool_memory_limit" {
  description = "Total memory limit (Gi) for CPU NodePool"
  type        = string
  default     = "200Gi"
}

variable "enable_capacity_block_nodepool" {
  description = "Enable Capacity Block NodePool for p-family instances (A100/H100)"
  type        = bool
  default     = true
}

variable "capacity_block_tags" {
  description = "Tags to match Capacity Block reservations"
  type        = map(string)
  default     = { "purpose" = "ml-training" }
}

variable "cb_nodepool_cpu_limit" {
  description = "Total CPU limit for Capacity Block NodePool"
  type        = number
  default     = 2000
}

variable "cb_nodepool_memory_limit" {
  description = "Total memory limit (Gi) for Capacity Block NodePool"
  type        = string
  default     = "8000Gi"
}

################################################################################
# EC2NodeClass for General Workloads
################################################################################

resource "kubectl_manifest" "ec2nodeclass_default" {
  yaml_body = yamlencode({
    apiVersion = "karpenter.k8s.aws/v1"
    kind       = "EC2NodeClass"
    metadata = {
      name = "default"
    }
    spec = {
      role = split("/", var.node_iam_role_arn)[1]

      # Karpenter v1 requires amiSelectorTerms
      # Using amiFamily with amiSelectorTerms for EKS-optimized AMI
      amiFamily = "AL2023"
      amiSelectorTerms = [
        {
          alias = "al2023@latest"
        }
      ]

      subnetSelectorTerms = [
        for subnet_id in var.subnet_ids : {
          id = subnet_id
        }
      ]

      securityGroupSelectorTerms = [
        for sg_id in var.security_group_ids : {
          id = sg_id
        }
      ]

      blockDeviceMappings = [
        {
          deviceName = "/dev/xvda"
          ebs = {
            volumeSize          = "100Gi"
            volumeType          = "gp3"
            encrypted           = true
            deleteOnTermination = true
          }
        }
      ]

      tags = merge(var.tags, {
        "karpenter.sh/discovery" = var.cluster_name
      })
    }
  })

  depends_on = [helm_release.karpenter]
}

################################################################################
# EC2NodeClass for GPU Workloads
################################################################################

resource "kubectl_manifest" "ec2nodeclass_gpu" {
  count = var.enable_gpu_nodepool ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "karpenter.k8s.aws/v1"
    kind       = "EC2NodeClass"
    metadata = {
      name = "gpu"
    }
    spec = {
      role = split("/", var.node_iam_role_arn)[1]

      # Use AL2023 for GPU nodes (AL2 not supported on EKS 1.33+)
      # Karpenter automatically selects GPU-optimized AMI variant for GPU instances
      amiFamily = "AL2023"
      amiSelectorTerms = [
        {
          alias = "al2023@latest"
        }
      ]

      subnetSelectorTerms = [
        for subnet_id in var.subnet_ids : {
          id = subnet_id
        }
      ]

      securityGroupSelectorTerms = [
        for sg_id in var.security_group_ids : {
          id = sg_id
        }
      ]

      # Larger disk for GPU workloads (model weights, datasets)
      blockDeviceMappings = [
        {
          deviceName = "/dev/xvda"
          ebs = {
            volumeSize          = "200Gi"
            volumeType          = "gp3"
            iops                = 4000
            throughput          = 250
            encrypted           = true
            deleteOnTermination = true
          }
        }
      ]

      tags = merge(var.tags, {
        "karpenter.sh/discovery" = var.cluster_name
        "workload"               = "gpu-training"
      })
    }
  })

  depends_on = [helm_release.karpenter]
}

################################################################################
# NodePool for General Workloads
################################################################################

resource "kubectl_manifest" "nodepool_default" {
  yaml_body = yamlencode({
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = "default"
    }
    spec = {
      template = {
        spec = {
          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = "default"
          }

          requirements = [
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = ["amd64"]
            },
            {
              key      = "kubernetes.io/os"
              operator = "In"
              values   = ["linux"]
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = ["spot", "on-demand"]
            },
            {
              key      = "node.kubernetes.io/instance-type"
              operator = "In"
              values   = var.cpu_instance_types
            }
          ]

          # Never expire — head pods for long-running training jobs live here
          expireAfter = "Never"
        }
      }

      limits = {
        cpu    = var.cpu_nodepool_cpu_limit
        memory = var.cpu_nodepool_memory_limit
      }

      disruption = {
        consolidationPolicy = "WhenEmptyOrUnderutilized"
        consolidateAfter    = "1m"
      }
    }
  })

  depends_on = [kubectl_manifest.ec2nodeclass_default]
}

################################################################################
# NodePool for GPU Training Workloads
################################################################################

resource "kubectl_manifest" "nodepool_gpu" {
  count = var.enable_gpu_nodepool ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = "gpu-training"
    }
    spec = {
      template = {
        metadata = {
          labels = {
            "nvidia.com/gpu"    = "true"
            "workload"          = "gpu-training"
          }
        }

        spec = {
          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = "gpu"
          }

          requirements = [
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = ["amd64"]
            },
            {
              key      = "kubernetes.io/os"
              operator = "In"
              values   = ["linux"]
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = ["spot", "on-demand"]  # Spot preferred for cost savings, On-Demand as fallback
            },
            {
              key      = "karpenter.k8s.aws/instance-category"
              operator = "In"
              values   = var.gpu_instance_categories  # g = A10G/L40S/A6000, p = A100/H100
            }
          ]

          # Taint GPU nodes to prevent non-GPU workloads
          taints = [
            {
              key    = "nvidia.com/gpu"
              value  = "true"
              effect = "NoSchedule"
            }
          ]

          # Don't expire GPU nodes (training jobs can be long)
          expireAfter = "Never"
        }
      }

      limits = {
        cpu    = var.gpu_nodepool_cpu_limit
        memory = var.gpu_nodepool_memory_limit
      }

      disruption = {
        # Don't disrupt GPU nodes during training
        consolidationPolicy = "WhenEmpty"
        consolidateAfter    = "5m"
      }

      # Prioritize GPU workloads
      weight = 100
    }
  })

  depends_on = [kubectl_manifest.ec2nodeclass_gpu]
}

################################################################################
# EC2NodeClass for Capacity Block (p-family: A100/H100)
################################################################################

resource "kubectl_manifest" "ec2nodeclass_capacity_block" {
  count = var.enable_capacity_block_nodepool ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "karpenter.k8s.aws/v1"
    kind       = "EC2NodeClass"
    metadata = {
      name = "gpu-capacity-block"
    }
    spec = {
      role = split("/", var.node_iam_role_arn)[1]

      amiFamily = "AL2023"
      amiSelectorTerms = [
        {
          alias = "al2023@latest"
        }
      ]

      subnetSelectorTerms = [
        for subnet_id in var.subnet_ids : {
          id = subnet_id
        }
      ]

      securityGroupSelectorTerms = [
        for sg_id in var.security_group_ids : {
          id = sg_id
        }
      ]

      # Select Capacity Block reservations by tag
      capacityReservationSelectorTerms = [
        {
          tags = var.capacity_block_tags
        }
      ]

      # Larger disk for GPU workloads (model weights, datasets)
      blockDeviceMappings = [
        {
          deviceName = "/dev/xvda"
          ebs = {
            volumeSize          = "200Gi"
            volumeType          = "gp3"
            iops                = 4000
            throughput          = 250
            encrypted           = true
            deleteOnTermination = true
          }
        }
      ]

      tags = merge(var.tags, {
        "karpenter.sh/discovery" = var.cluster_name
        "workload"               = "gpu-training"
      })
    }
  })

  depends_on = [helm_release.karpenter]
}

################################################################################
# NodePool for Capacity Block (p-family: A100/H100)
################################################################################

resource "kubectl_manifest" "nodepool_capacity_block" {
  count = var.enable_capacity_block_nodepool ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = "gpu-capacity-block"
    }
    spec = {
      template = {
        metadata = {
          labels = {
            "nvidia.com/gpu" = "true"
            "workload"       = "gpu-training"
          }
        }

        spec = {
          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = "gpu-capacity-block"
          }

          requirements = [
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = ["amd64"]
            },
            {
              key      = "kubernetes.io/os"
              operator = "In"
              values   = ["linux"]
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = ["reserved"]
            },
            {
              key      = "karpenter.k8s.aws/instance-category"
              operator = "In"
              values   = ["p"]
            }
          ]

          # Taint GPU nodes to prevent non-GPU workloads
          taints = [
            {
              key    = "nvidia.com/gpu"
              value  = "true"
              effect = "NoSchedule"
            }
          ]

          # Don't expire GPU nodes (training jobs can be long)
          expireAfter = "Never"
        }
      }

      limits = {
        cpu    = var.cb_nodepool_cpu_limit
        memory = var.cb_nodepool_memory_limit
      }

      disruption = {
        consolidationPolicy = "WhenEmpty"
        consolidateAfter    = "5m"
      }

      # Prioritize GPU workloads
      weight = 100
    }
  })

  depends_on = [kubectl_manifest.ec2nodeclass_capacity_block]
}
