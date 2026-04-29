################################################################################
# S3 Module for ML Training Storage
################################################################################
# Creates S3 bucket for Ray checkpoints and model outputs with IRSA for
# secure pod access without embedding credentials.
################################################################################

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

variable "oidc_provider_arn" {
  description = "OIDC provider ARN for EKS cluster"
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace for training pods"
  type        = string
  default     = "ml-training"
}

variable "service_account_name" {
  description = "Kubernetes service account name"
  type        = string
  default     = "ray-training-sa"
}

variable "bucket_prefix" {
  description = "Prefix for S3 bucket name"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}

################################################################################
# Data Sources
################################################################################

data "aws_region" "current" {}

# Extract account ID and OIDC provider URL from ARN
# OIDC ARN format: arn:aws:iam::<account_id>:oidc-provider/...
locals {
  account_id    = regex("arn:aws:iam::([0-9]+):oidc-provider", var.oidc_provider_arn)[0]
  oidc_provider = replace(var.oidc_provider_arn, "/^arn:aws:iam::[0-9]+:oidc-provider\\//", "")
  bucket_name   = var.bucket_prefix != "" ? "${var.bucket_prefix}-ml-training" : "${var.cluster_name}-${local.account_id}-ml-training"
}

################################################################################
# S3 Bucket
################################################################################

resource "aws_s3_bucket" "training" {
  bucket = local.bucket_name

  tags = merge(var.tags, {
    Name    = local.bucket_name
    Purpose = "ML Training Storage"
  })
}

# Enable versioning for checkpoint safety
resource "aws_s3_bucket_versioning" "training" {
  bucket = aws_s3_bucket.training.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Server-side encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "training" {
  bucket = aws_s3_bucket.training.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Block public access
resource "aws_s3_bucket_public_access_block" "training" {
  bucket = aws_s3_bucket.training.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

################################################################################
# IAM Role for Service Account (IRSA)
################################################################################

# Trust policy allowing the service account to assume this role
data "aws_iam_policy_document" "training_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:sub"
      values   = ["system:serviceaccount:${var.namespace}:${var.service_account_name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "training" {
  name               = "${var.cluster_name}-training-s3"
  assume_role_policy = data.aws_iam_policy_document.training_assume_role.json

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-training-s3"
  })
}

# S3 access policy
data "aws_iam_policy_document" "training_s3" {
  # List bucket
  statement {
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.training.arn]
  }

  # Object operations
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:GetObjectVersion",
    ]
    resources = ["${aws_s3_bucket.training.arn}/*"]
  }

  # Multipart upload operations (required for large checkpoints)
  statement {
    effect = "Allow"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.training.arn}/*"]
  }
}

resource "aws_iam_policy" "training_s3" {
  name        = "${var.cluster_name}-training-s3"
  description = "S3 access for ML training pods"
  policy      = data.aws_iam_policy_document.training_s3.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "training_s3" {
  role       = aws_iam_role.training.name
  policy_arn = aws_iam_policy.training_s3.arn
}

################################################################################
# Outputs
################################################################################

output "bucket_name" {
  description = "S3 bucket name"
  value       = aws_s3_bucket.training.id
}

output "bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.training.arn
}

output "training_role_arn" {
  description = "IAM role ARN for training service account (IRSA)"
  value       = aws_iam_role.training.arn
}

output "training_role_name" {
  description = "IAM role name for training service account"
  value       = aws_iam_role.training.name
}

output "ray_storage_path" {
  description = "S3 path for Ray storage"
  value       = "s3://${aws_s3_bucket.training.id}/ray"
}

output "output_path" {
  description = "S3 path for model outputs"
  value       = "s3://${aws_s3_bucket.training.id}/outputs"
}
