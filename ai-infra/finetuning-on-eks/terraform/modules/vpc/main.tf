################################################################################
# VPC Module for EKS
################################################################################

variable "name" {
  description = "Name prefix for resources"
  type        = string
}

variable "cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "azs" {
  description = "Availability zones"
  type        = list(string)
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

variable "cluster_name" {
  description = "EKS cluster name for Karpenter subnet discovery"
  type        = string
  default     = ""
}

locals {
  public_subnets  = [for i, az in var.azs : cidrsubnet(var.cidr, 4, i)]
  private_subnets = [for i, az in var.azs : cidrsubnet(var.cidr, 4, i + length(var.azs))]
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = var.name
  cidr = var.cidr

  azs             = var.azs
  public_subnets  = local.public_subnets
  private_subnets = local.private_subnets

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  enable_dns_support   = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }

  private_subnet_tags = merge(
    {
      "kubernetes.io/role/internal-elb" = 1
    },
    var.cluster_name != "" ? {
      "karpenter.sh/discovery" = var.cluster_name
    } : {}
  )

  tags = var.tags
}

output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "private_subnets" {
  description = "Private subnet IDs"
  value       = module.vpc.private_subnets
}

output "public_subnets" {
  description = "Public subnet IDs"
  value       = module.vpc.public_subnets
}

output "vpc_cidr_block" {
  description = "VPC CIDR block"
  value       = module.vpc.vpc_cidr_block
}
