################################################################################
# Cilium CNI Module (Chaining Mode with AWS VPC CNI)
################################################################################
#
# This module deploys Cilium in chaining mode alongside AWS VPC CNI:
# - AWS VPC CNI handles IPAM (IP address allocation from VPC)
# - Cilium provides advanced network policies, observability, and security
#
# Benefits of chaining mode:
# - Native VPC networking (no overlay, full performance)
# - Advanced L7 network policies
# - Hubble observability (network flows, DNS, HTTP)
# - eBPF-based load balancing
################################################################################

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "cluster_endpoint" {
  description = "EKS cluster endpoint"
  type        = string
}

variable "cluster_certificate_authority_data" {
  description = "EKS cluster CA data"
  type        = string
}

variable "cilium_version" {
  description = "Cilium Helm chart version"
  type        = string
  default     = "1.16.5"
}

variable "enable_hubble" {
  description = "Enable Hubble observability"
  type        = bool
  default     = true
}

variable "enable_hubble_ui" {
  description = "Enable Hubble UI"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

################################################################################
# Cilium Helm Release
################################################################################

resource "helm_release" "cilium" {
  name       = "cilium"
  repository = "https://helm.cilium.io/"
  chart      = "cilium"
  version    = var.cilium_version
  namespace  = "kube-system"

  # Chaining mode configuration
  # AWS VPC CNI remains the primary CNI for IPAM
  # Cilium chains on top for policy and observability
  values = [
    yamlencode({
      # Chaining mode with AWS VPC CNI
      cni = {
        chainingMode = "aws-cni"
        exclusive    = false
      }

      # Use ENI routing (native VPC routing)
      routingMode = "native"

      # Disable masquerading since VPC CNI handles it
      enableIPv4Masquerade = false

      # EKS-specific settings
      eks = {
        enabled = true
      }

      # Use eBPF for kube-proxy replacement (optional but recommended)
      kubeProxyReplacement = "false"

      # Hubble observability
      hubble = {
        enabled = var.enable_hubble
        relay = {
          enabled = var.enable_hubble
        }
        ui = {
          enabled = var.enable_hubble_ui
        }
        metrics = {
          enabled = var.enable_hubble ? ["dns", "drop", "tcp", "flow", "icmp", "http"] : []
        }
      }

      # Operator settings
      operator = {
        replicas = 1
      }

      # Resource requests/limits
      resources = {
        requests = {
          cpu    = "100m"
          memory = "128Mi"
        }
        limits = {
          cpu    = "1000m"
          memory = "1Gi"
        }
      }

      # Enable policy enforcement
      policyEnforcementMode = "default"

      # Cluster identification
      cluster = {
        name = var.cluster_name
      }
    })
  ]

  # Wait for deployment to complete
  wait    = true
  timeout = 600

  # Cilium needs to be deployed after the cluster is ready
  depends_on = []
}

################################################################################
# Outputs
################################################################################

output "cilium_release_name" {
  description = "Cilium Helm release name"
  value       = helm_release.cilium.name
}

output "cilium_release_namespace" {
  description = "Cilium namespace"
  value       = helm_release.cilium.namespace
}

output "cilium_version" {
  description = "Cilium version deployed"
  value       = var.cilium_version
}
