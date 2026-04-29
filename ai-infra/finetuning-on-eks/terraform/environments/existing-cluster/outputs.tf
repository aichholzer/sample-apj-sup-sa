################################################################################
# Outputs - Existing Cluster Environment
################################################################################

output "cluster_name" {
  description = "EKS cluster name"
  value       = var.cluster_name
}

output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = data.aws_eks_cluster.this.endpoint
}

output "region" {
  description = "AWS region"
  value       = var.region
}

output "configure_kubectl" {
  description = "Command to configure kubectl"
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${var.cluster_name}"
}

output "karpenter_enabled" {
  description = "Whether Karpenter is enabled"
  value       = var.enable_karpenter
}

output "karpenter_node_role_arn" {
  description = "Karpenter node IAM role ARN"
  value       = var.enable_karpenter ? aws_iam_role.karpenter_node[0].arn : null
}

output "kueue_enabled" {
  description = "Whether Kueue is enabled"
  value       = var.enable_kueue
}

output "efs_file_system_id" {
  description = "EFS file system ID for shared ML data"
  value       = var.enable_efs ? module.efs[0].file_system_id : (var.existing_efs_file_system_id != "" ? var.existing_efs_file_system_id : null)
}

output "efs_storage_class" {
  description = "Kubernetes StorageClass name for EFS"
  value       = var.enable_efs ? module.efs[0].storage_class_name : null
}

output "s3_bucket_name" {
  description = "S3 bucket name for training storage"
  value       = var.enable_s3 ? module.s3[0].bucket_name : null
}

output "s3_training_role_arn" {
  description = "IAM role ARN for training service account (IRSA)"
  value       = var.enable_s3 ? module.s3[0].training_role_arn : null
}

output "ray_storage_path" {
  description = "S3 path for Ray storage"
  value       = var.enable_s3 ? module.s3[0].ray_storage_path : null
}

output "s3_output_path" {
  description = "S3 path for model outputs"
  value       = var.enable_s3 ? module.s3[0].output_path : null
}

output "kuberay_namespace" {
  description = "Namespace where KubeRay operator is installed"
  value       = var.enable_kuberay ? module.kuberay[0].operator_namespace : null
}

output "kueue_namespace" {
  description = "Namespace where Kueue is installed"
  value       = var.enable_kueue ? module.kueue[0].namespace : null
}

output "cilium_enabled" {
  description = "Whether Cilium CNI is enabled"
  value       = var.enable_cilium
}
