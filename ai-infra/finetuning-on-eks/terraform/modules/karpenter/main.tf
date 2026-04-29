################################################################################
# Karpenter Module
################################################################################
#
# This module deploys Karpenter for dynamic node provisioning:
# - IRSA role for Karpenter controller
# - SQS queue for spot interruption handling
# - Instance profile for provisioned nodes
# - Karpenter Helm release
#
# Karpenter benefits over Cluster Autoscaler:
# - Faster scaling (seconds vs minutes)
# - Right-sizing (picks optimal instance types)
# - Spot interruption handling
# - Consolidation (bin packing)
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

variable "oidc_provider_arn" {
  description = "OIDC provider ARN for IRSA"
  type        = string
}

variable "node_iam_role_arn" {
  description = "IAM role ARN for Karpenter-provisioned nodes"
  type        = string
}

variable "karpenter_version" {
  description = "Karpenter Helm chart version"
  type        = string
  default     = "1.9.0"
}

variable "enable_spot_termination" {
  description = "Enable spot termination handling"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

################################################################################
# Data Sources
################################################################################

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_iam_policy_document" "karpenter_controller_assume_role" {
  statement {
    effect = "Allow"
    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }
    actions = ["sts:AssumeRoleWithWebIdentity"]
    condition {
      test     = "StringEquals"
      variable = "${replace(var.oidc_provider_arn, "/^(.*provider/)/", "")}:sub"
      values   = ["system:serviceaccount:karpenter:karpenter"]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(var.oidc_provider_arn, "/^(.*provider/)/", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

################################################################################
# IAM Role for Karpenter Controller
################################################################################

resource "aws_iam_role" "karpenter_controller" {
  name               = "${var.cluster_name}-karpenter-controller"
  assume_role_policy = data.aws_iam_policy_document.karpenter_controller_assume_role.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "karpenter_controller" {
  name = "KarpenterController"
  role = aws_iam_role.karpenter_controller.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowScopedEC2InstanceAccessActions"
        Effect = "Allow"
        Action = [
          "ec2:RunInstances",
          "ec2:CreateFleet"
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}::image/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}::snapshot/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:security-group/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:subnet/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:launch-template/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:capacity-reservation/*"
        ]
      },
      {
        Sid    = "AllowScopedEC2InstanceActionsWithTags"
        Effect = "Allow"
        Action = [
          "ec2:RunInstances",
          "ec2:CreateFleet",
          "ec2:CreateLaunchTemplate"
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:fleet/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:instance/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:volume/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:network-interface/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:launch-template/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:spot-instances-request/*"
        ]
        Condition = {
          StringEquals = {
            "aws:RequestTag/kubernetes.io/cluster/${var.cluster_name}" = "owned"
          }
          StringLike = {
            "aws:RequestTag/karpenter.sh/nodepool" = "*"
          }
        }
      },
      {
        Sid    = "AllowScopedResourceCreationTagging"
        Effect = "Allow"
        Action = "ec2:CreateTags"
        Resource = [
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:fleet/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:instance/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:volume/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:network-interface/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:launch-template/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:spot-instances-request/*"
        ]
        Condition = {
          StringEquals = {
            "aws:RequestTag/kubernetes.io/cluster/${var.cluster_name}" = "owned"
            "ec2:CreateAction" = [
              "RunInstances",
              "CreateFleet",
              "CreateLaunchTemplate"
            ]
          }
          StringLike = {
            "aws:RequestTag/karpenter.sh/nodepool" = "*"
          }
        }
      },
      {
        Sid    = "AllowScopedResourceTagging"
        Effect = "Allow"
        Action = "ec2:CreateTags"
        Resource = "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:instance/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/kubernetes.io/cluster/${var.cluster_name}" = "owned"
          }
          StringLike = {
            "aws:ResourceTag/karpenter.sh/nodepool" = "*"
          }
          "ForAllValues:StringEquals" = {
            "aws:TagKeys" = ["karpenter.sh/nodeclaim", "Name"]
          }
        }
      },
      {
        Sid    = "AllowScopedDeletion"
        Effect = "Allow"
        Action = [
          "ec2:TerminateInstances",
          "ec2:DeleteLaunchTemplate"
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:instance/*",
          "arn:${data.aws_partition.current.partition}:ec2:${data.aws_region.current.name}:*:launch-template/*"
        ]
        Condition = {
          StringEquals = {
            "aws:ResourceTag/kubernetes.io/cluster/${var.cluster_name}" = "owned"
          }
          StringLike = {
            "aws:ResourceTag/karpenter.sh/nodepool" = "*"
          }
        }
      },
      {
        Sid    = "AllowRegionalReadActions"
        Effect = "Allow"
        Action = [
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeImages",
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceTypeOfferings",
          "ec2:DescribeInstanceTypes",
          "ec2:DescribeLaunchTemplates",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSpotPriceHistory",
          "ec2:DescribeSubnets",
          "ec2:DescribeCapacityReservations"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = data.aws_region.current.name
          }
        }
      },
      {
        Sid      = "AllowSSMReadActions"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:${data.aws_partition.current.partition}:ssm:${data.aws_region.current.name}::parameter/aws/service/*"
      },
      {
        Sid      = "AllowPricingReadActions"
        Effect   = "Allow"
        Action   = "pricing:GetProducts"
        Resource = "*"
      },
      {
        Sid    = "AllowPassingInstanceRole"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = var.node_iam_role_arn
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ec2.amazonaws.com"
          }
        }
      },
      {
        Sid      = "AllowScopedInstanceProfileCreationActions"
        Effect   = "Allow"
        Action   = "iam:CreateInstanceProfile"
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:RequestTag/kubernetes.io/cluster/${var.cluster_name}" = "owned"
            "aws:RequestTag/topology.kubernetes.io/region"             = data.aws_region.current.name
          }
          StringLike = {
            "aws:RequestTag/karpenter.k8s.aws/ec2nodeclass" = "*"
          }
        }
      },
      {
        Sid      = "AllowScopedInstanceProfileTagActions"
        Effect   = "Allow"
        Action   = "iam:TagInstanceProfile"
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/kubernetes.io/cluster/${var.cluster_name}" = "owned"
            "aws:ResourceTag/topology.kubernetes.io/region"             = data.aws_region.current.name
            "aws:RequestTag/kubernetes.io/cluster/${var.cluster_name}"  = "owned"
            "aws:RequestTag/topology.kubernetes.io/region"              = data.aws_region.current.name
          }
          StringLike = {
            "aws:ResourceTag/karpenter.k8s.aws/ec2nodeclass" = "*"
            "aws:RequestTag/karpenter.k8s.aws/ec2nodeclass"  = "*"
          }
        }
      },
      {
        Sid    = "AllowScopedInstanceProfileActions"
        Effect = "Allow"
        Action = [
          "iam:AddRoleToInstanceProfile",
          "iam:RemoveRoleFromInstanceProfile",
          "iam:DeleteInstanceProfile"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/kubernetes.io/cluster/${var.cluster_name}" = "owned"
            "aws:ResourceTag/topology.kubernetes.io/region"             = data.aws_region.current.name
          }
          StringLike = {
            "aws:ResourceTag/karpenter.k8s.aws/ec2nodeclass" = "*"
          }
        }
      },
      {
        Sid      = "AllowInstanceProfileReadActions"
        Effect   = "Allow"
        Action   = "iam:GetInstanceProfile"
        Resource = "*"
      },
      {
        Sid      = "AllowAPIServerEndpointDiscovery"
        Effect   = "Allow"
        Action   = "eks:DescribeCluster"
        Resource = "arn:${data.aws_partition.current.partition}:eks:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:cluster/${var.cluster_name}"
      }
    ]
  })
}

################################################################################
# SQS Queue for Spot Interruption Handling
################################################################################

resource "aws_sqs_queue" "karpenter" {
  count = var.enable_spot_termination ? 1 : 0

  name                      = "${var.cluster_name}-karpenter"
  message_retention_seconds = 300
  sqs_managed_sse_enabled   = true

  tags = var.tags
}

resource "aws_sqs_queue_policy" "karpenter" {
  count = var.enable_spot_termination ? 1 : 0

  queue_url = aws_sqs_queue.karpenter[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "SqsWrite"
        Effect    = "Allow"
        Principal = { Service = ["events.amazonaws.com", "sqs.amazonaws.com"] }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.karpenter[0].arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "karpenter_sqs" {
  count = var.enable_spot_termination ? 1 : 0

  name = "KarpenterSQS"
  role = aws_iam_role.karpenter_controller.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowInterruptionQueueActions"
        Effect   = "Allow"
        Action   = ["sqs:DeleteMessage", "sqs:GetQueueUrl", "sqs:ReceiveMessage"]
        Resource = aws_sqs_queue.karpenter[0].arn
      }
    ]
  })
}

################################################################################
# EventBridge Rules for Spot Interruption
################################################################################

resource "aws_cloudwatch_event_rule" "karpenter_instance_rebalance" {
  count = var.enable_spot_termination ? 1 : 0

  name        = "${var.cluster_name}-karpenter-instance-rebalance"
  description = "Karpenter EC2 Instance Rebalance Recommendation"

  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Instance Rebalance Recommendation"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "karpenter_instance_rebalance" {
  count = var.enable_spot_termination ? 1 : 0

  rule      = aws_cloudwatch_event_rule.karpenter_instance_rebalance[0].name
  target_id = "KarpenterInterruptionQueueTarget"
  arn       = aws_sqs_queue.karpenter[0].arn
}

resource "aws_cloudwatch_event_rule" "karpenter_instance_state_change" {
  count = var.enable_spot_termination ? 1 : 0

  name        = "${var.cluster_name}-karpenter-instance-state-change"
  description = "Karpenter EC2 Instance State Change"

  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Instance State-change Notification"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "karpenter_instance_state_change" {
  count = var.enable_spot_termination ? 1 : 0

  rule      = aws_cloudwatch_event_rule.karpenter_instance_state_change[0].name
  target_id = "KarpenterInterruptionQueueTarget"
  arn       = aws_sqs_queue.karpenter[0].arn
}

resource "aws_cloudwatch_event_rule" "karpenter_spot_interruption" {
  count = var.enable_spot_termination ? 1 : 0

  name        = "${var.cluster_name}-karpenter-spot-interruption"
  description = "Karpenter EC2 Spot Instance Interruption Warning"

  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Spot Instance Interruption Warning"]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "karpenter_spot_interruption" {
  count = var.enable_spot_termination ? 1 : 0

  rule      = aws_cloudwatch_event_rule.karpenter_spot_interruption[0].name
  target_id = "KarpenterInterruptionQueueTarget"
  arn       = aws_sqs_queue.karpenter[0].arn
}

################################################################################
# Karpenter Helm Release
################################################################################

resource "helm_release" "karpenter" {
  name       = "karpenter"
  repository = "oci://public.ecr.aws/karpenter"
  chart      = "karpenter"
  version    = var.karpenter_version
  namespace  = "karpenter"

  create_namespace = true

  values = [
    yamlencode({
      settings = {
        clusterName       = var.cluster_name
        clusterEndpoint   = var.cluster_endpoint
        interruptionQueue = var.enable_spot_termination ? aws_sqs_queue.karpenter[0].name : ""
        featureGates = {
          reservedCapacity = true
        }
      }

      serviceAccount = {
        annotations = {
          "eks.amazonaws.com/role-arn" = aws_iam_role.karpenter_controller.arn
        }
      }

      controller = {
        resources = {
          requests = {
            cpu    = "100m"
            memory = "256Mi"
          }
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }
      }

      # Run on system nodes (not Karpenter-managed nodes)
      affinity = {
        nodeAffinity = {
          requiredDuringSchedulingIgnoredDuringExecution = {
            nodeSelectorTerms = [
              {
                matchExpressions = [
                  {
                    key      = "karpenter.sh/nodepool"
                    operator = "DoesNotExist"
                  }
                ]
              }
            ]
          }
        }
      }

      tolerations = [
        {
          key      = "CriticalAddonsOnly"
          operator = "Exists"
        }
      ]
    })
  ]

  wait    = true
  timeout = 600
}

################################################################################
# Outputs
################################################################################

output "karpenter_role_arn" {
  description = "Karpenter controller IAM role ARN"
  value       = aws_iam_role.karpenter_controller.arn
}

output "karpenter_queue_name" {
  description = "SQS queue name for spot interruption handling"
  value       = var.enable_spot_termination ? aws_sqs_queue.karpenter[0].name : null
}

output "karpenter_release_name" {
  description = "Karpenter Helm release name"
  value       = helm_release.karpenter.name
}

output "karpenter_namespace" {
  description = "Karpenter namespace"
  value       = helm_release.karpenter.namespace
}
