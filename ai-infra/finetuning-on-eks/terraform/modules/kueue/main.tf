################################################################################
# Kueue Module
################################################################################
# Deploys Kueue for gang scheduling and job queuing on Kubernetes
# - Enables RayJob integration for atomic worker scheduling
# - Prevents deadlocks where partial allocations hold resources
# - Supports gang scheduling for distributed training workloads
################################################################################

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

variable "kueue_version" {
  description = "Kueue Helm chart version"
  type        = string
  default     = "0.16.0"
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

################################################################################
# Kueue Namespace
################################################################################

resource "kubernetes_namespace_v1" "kueue_system" {
  metadata {
    name = "kueue-system"

    labels = {
      "app.kubernetes.io/name"       = "kueue"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

################################################################################
# Kueue Helm Release
################################################################################

resource "helm_release" "kueue" {
  name       = "kueue"
  repository = "oci://registry.k8s.io/kueue/charts"
  chart      = "kueue"
  version    = var.kueue_version
  namespace  = kubernetes_namespace_v1.kueue_system.metadata[0].name

  # Enable RayJob integration for gang scheduling
  set {
    name  = "enabledIntegrations.rayJob"
    value = "true"
  }

  # Resource limits for controller
  set {
    name  = "controllerManager.resources.limits.cpu"
    value = "500m"
  }

  set {
    name  = "controllerManager.resources.limits.memory"
    value = "512Mi"
  }

  set {
    name  = "controllerManager.resources.requests.cpu"
    value = "100m"
  }

  set {
    name  = "controllerManager.resources.requests.memory"
    value = "128Mi"
  }

  depends_on = [kubernetes_namespace_v1.kueue_system]
}

################################################################################
# Outputs
################################################################################

output "namespace" {
  description = "Namespace where Kueue is installed"
  value       = kubernetes_namespace_v1.kueue_system.metadata[0].name
}

output "kueue_version" {
  description = "Installed Kueue version"
  value       = var.kueue_version
}
