################################################################################
# KubeRay Operator Module
################################################################################
# Deploys KubeRay operator for managing Ray clusters on Kubernetes
# - Manages RayCluster and RayJob CRDs
# - Enables distributed training with Ray Train
################################################################################

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

variable "kuberay_version" {
  description = "KubeRay operator Helm chart version"
  type        = string
  default     = "1.2.2"
}

variable "ray_version" {
  description = "Default Ray version for clusters"
  type        = string
  default     = "2.9.3"
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

################################################################################
# KubeRay Operator Namespace
################################################################################

resource "kubernetes_namespace_v1" "kuberay_system" {
  metadata {
    name = "kuberay-system"

    labels = {
      "app.kubernetes.io/name"       = "kuberay"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

################################################################################
# KubeRay Operator Helm Release
################################################################################

resource "helm_release" "kuberay_operator" {
  name       = "kuberay-operator"
  repository = "https://ray-project.github.io/kuberay-helm/"
  chart      = "kuberay-operator"
  version    = var.kuberay_version
  namespace  = kubernetes_namespace_v1.kuberay_system.metadata[0].name

  # Operator settings
  set {
    name  = "image.pullPolicy"
    value = "IfNotPresent"
  }

  # Resource limits for operator
  set {
    name  = "resources.limits.cpu"
    value = "500m"
  }

  set {
    name  = "resources.limits.memory"
    value = "512Mi"
  }

  set {
    name  = "resources.requests.cpu"
    value = "100m"
  }

  set {
    name  = "resources.requests.memory"
    value = "128Mi"
  }

  # Watch all namespaces
  set {
    name  = "watchNamespace"
    value = ""
  }

  # Enable leader election for HA
  set {
    name  = "leaderElection.enabled"
    value = "true"
  }

  depends_on = [kubernetes_namespace_v1.kuberay_system]
}

################################################################################
# Outputs
################################################################################

output "operator_namespace" {
  description = "Namespace where KubeRay operator is installed"
  value       = kubernetes_namespace_v1.kuberay_system.metadata[0].name
}

output "kuberay_version" {
  description = "Installed KubeRay version"
  value       = var.kuberay_version
}

output "ray_version" {
  description = "Default Ray version"
  value       = var.ray_version
}
