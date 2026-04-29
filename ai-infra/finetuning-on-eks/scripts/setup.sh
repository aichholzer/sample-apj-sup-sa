#!/bin/bash
set -euo pipefail

# ============================================================================
# Fine-tuning on EKS - Setup Script
# ============================================================================
# Single script to deploy infrastructure and run distributed training jobs.
#
# Quick Start:
#   ./setup.sh deploy                 # Deploy EKS + Karpenter + KubeRay
#   ./setup.sh build-push             # Build and push Docker image
#   ./setup.sh configure              # Configure training (interactive)
#   ./setup.sh train                  # Start training
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OVERLAYS_DIR="${PROJECT_ROOT}/kubernetes/overlays"
STATE_FILE="${PROJECT_ROOT}/.current-overlay"
TF_ENV_FILE="${PROJECT_ROOT}/.terraform-env"

# Get Terraform environment directory
get_tf_env_dir() {
    local env="${FT_TF_ENV:-}"
    if [ -z "$env" ] && [ -f "$TF_ENV_FILE" ]; then
        env=$(cat "$TF_ENV_FILE")
    fi
    echo "${PROJECT_ROOT}/terraform/environments/${env:-dev}"
}

# Set Terraform environment
set_tf_env() {
    local env="$1"
    local env_dir="${PROJECT_ROOT}/terraform/environments/${env}"
    if [ ! -d "$env_dir" ]; then
        log_error "Environment not found: ${env}"
        echo "Available environments:"
        ls -1 "${PROJECT_ROOT}/terraform/environments/"
        exit 1
    fi
    echo "$env" > "$TF_ENV_FILE"
    log_info "Terraform environment set to: ${env}"
    log_info "Directory: ${env_dir}"
}

# Get current overlay (model)
get_current_overlay() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo "tinyllama-1b"  # Default
    fi
}

# Set current overlay
set_current_overlay() {
    echo "$1" > "$STATE_FILE"
}

# Get kustomization file for current or specified overlay
get_kustomization() {
    local overlay="${1:-$(get_current_overlay)}"
    echo "${OVERLAYS_DIR}/${overlay}/kustomization.yaml"
}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# ============================================================================
# Prerequisites Check
# ============================================================================
check_prerequisites() {
    log_info "Checking prerequisites..."

    local missing=()

    command -v aws >/dev/null 2>&1 || missing+=("aws-cli")
    command -v terraform >/dev/null 2>&1 || missing+=("terraform")
    command -v kubectl >/dev/null 2>&1 || missing+=("kubectl")
    command -v docker >/dev/null 2>&1 || missing+=("docker")

    if [ ${#missing[@]} -ne 0 ]; then
        log_error "Missing required tools: ${missing[*]}"
        echo ""
        echo "Install instructions:"
        echo "  aws-cli:   https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
        echo "  terraform: https://developer.hashicorp.com/terraform/downloads"
        echo "  kubectl:   https://kubernetes.io/docs/tasks/tools/"
        echo "  docker:    https://docs.docker.com/get-docker/"
        exit 1
    fi

    # Check AWS credentials
    if ! aws sts get-caller-identity >/dev/null 2>&1; then
        log_error "AWS credentials not configured. Run 'aws configure' first."
        exit 1
    fi

    log_info "All prerequisites met!"
}

# ============================================================================
# Infrastructure Deployment
# ============================================================================
deploy_infrastructure() {
    log_info "Deploying infrastructure..."

    cd "$(get_tf_env_dir)"

    log_step "Initializing Terraform..."
    terraform init

    log_step "Planning infrastructure..."
    terraform plan -out=tfplan

    log_step "Applying infrastructure..."
    terraform apply tfplan

    # Configure kubectl
    log_step "Configuring kubectl..."
    local region=$(terraform output -raw region 2>/dev/null || echo 'us-west-2')
    local cluster_name=$(terraform output -raw cluster_name)

    aws eks update-kubeconfig --region "${region}" --name "${cluster_name}"

    log_info "Infrastructure deployed successfully!"
    echo ""
    echo "Deployed components:"
    echo "  - EKS Cluster: ${cluster_name}"
    echo "  - Karpenter: Auto-scales GPU nodes"
    echo "  - KubeRay: Manages Ray clusters"
    echo "  - Kueue: Gang scheduling for distributed training"
    echo "  - EFS: Shared storage for HuggingFace cache"
    echo "  - S3: Checkpoints and model outputs"
    echo ""
    echo "Next steps:"
    echo "  1. ./setup.sh build-push     # Build and push Docker image"
    echo "  2. ./setup.sh configure-s3   # Configure S3 storage"
    echo "  3. ./setup.sh configure      # Configure training"
    echo "  4. ./setup.sh train          # Start training"
}

# ============================================================================
# Docker Image Build
# ============================================================================
build_image() {
    local push="${1:-false}"
    log_info "Building Docker image..."

    cd "${PROJECT_ROOT}"

    if [ "$push" = "true" ]; then
        "${PROJECT_ROOT}/docker/build.sh" --push

        echo ""
        log_info "Update kubernetes/components/image/kustomization.yaml with the image URL shown above."
    else
        "${PROJECT_ROOT}/docker/build.sh"
    fi
}

# ============================================================================
# HuggingFace Token Management
# ============================================================================
HF_SECRET_NAME="hf-token"
HF_SECRET_NAMESPACE="ml-training"

# Check if HF token secret exists
check_hf_secret() {
    kubectl get secret "$HF_SECRET_NAME" -n "$HF_SECRET_NAMESPACE" >/dev/null 2>&1
}

# Create or update HF token secret
setup_hf_token() {
    local token="$1"

    # Ensure namespace exists
    kubectl create namespace "$HF_SECRET_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1

    # Create or update secret
    kubectl create secret generic "$HF_SECRET_NAME" \
        --namespace "$HF_SECRET_NAMESPACE" \
        --from-literal=token="$token" \
        --dry-run=client -o yaml | kubectl apply -f -

    log_info "HuggingFace token secret created/updated."
}

# Delete HF token secret
delete_hf_token() {
    kubectl delete secret "$HF_SECRET_NAME" -n "$HF_SECRET_NAMESPACE" --ignore-not-found
    log_info "HuggingFace token secret deleted."
}

# Prompt for HF token setup
prompt_hf_token() {
    local model="$1"

    # Models that require HF token
    local gated_models=("llama-7b" "llama-13b" "llama-70b")
    local needs_token=false

    for m in "${gated_models[@]}"; do
        if [ "$m" = "$model" ]; then
            needs_token=true
            break
        fi
    done

    if [ "$needs_token" = "true" ]; then
        echo ""
        log_warn "Model '$model' requires HuggingFace authentication."

        if check_hf_secret; then
            echo "  HuggingFace token secret already exists."
            read -p "  Update token? (y/N): " update_token
            if [ "$update_token" = "y" ] || [ "$update_token" = "Y" ]; then
                read -sp "  Enter HuggingFace token: " hf_token
                echo ""
                if [ -n "$hf_token" ]; then
                    setup_hf_token "$hf_token"
                fi
            fi
        else
            echo "  No HuggingFace token configured."
            echo "  Get your token at: https://huggingface.co/settings/tokens"
            echo ""
            read -p "  Enter HuggingFace token (or press Enter to skip): " hf_token
            if [ -n "$hf_token" ]; then
                setup_hf_token "$hf_token"
            else
                log_warn "Skipped. Training may fail without a valid token."
                echo "  Run './setup.sh hf-token' later to set up the token."
            fi
        fi
    fi
}

# ============================================================================
# Training Configuration
# ============================================================================

# Available overlays and settings
MODELS=("tinyllama-1b" "llama-7b" "llama-13b" "llama-70b")
SETTINGS=("quick-test" "full-training")

# Validate component
validate_component() {
    local type="$1"
    local value="$2"
    shift 2
    local valid_values=("$@")

    for v in "${valid_values[@]}"; do
        if [ "$v" = "$value" ]; then
            return 0
        fi
    done

    log_error "Invalid ${type}: ${value}"
    echo "Valid options: ${valid_values[*]}"
    exit 1
}

# Configure training
configure_training() {
    local model="${1:-}"
    local settings="${2:-}"

    # Interactive mode if no model provided
    if [ -z "$model" ]; then
        echo ""
        echo "=========================================="
        echo "  Training Configuration"
        echo "=========================================="
        echo ""
        echo "Select model (with optimal defaults):"
        echo "  1. tinyllama-1b  [single-gpu + qlora]     - Fast testing"
        echo "  2. llama-7b      [single-gpu + qlora]     - Memory efficient"
        echo "  3. llama-13b     [multi-gpu + lora]       - Multi-GPU training"
        echo "  4. llama-70b     [multi-node + fsdp]      - Distributed training"
        read -p "Select model [1-4]: " model_idx
        model="${MODELS[$((model_idx-1))]}"

        echo ""
        echo "Select settings:"
        echo "  1. quick-test     (100 samples, 1 epoch - for validation)"
        echo "  2. full-training  (all samples, 3 epochs - for production)"
        echo "  3. none           (use base defaults)"
        read -p "Select settings [1-3]: " settings_idx
        case $settings_idx in
            1) settings="quick-test" ;;
            2) settings="full-training" ;;
            *) settings="" ;;
        esac
        echo ""
    fi

    # Validate model
    validate_component "model" "$model" "${MODELS[@]}"

    # Validate settings if provided
    if [ -n "$settings" ]; then
        validate_component "settings" "$settings" "${SETTINGS[@]}"
    fi

    # Check overlay exists
    local kustomization
    kustomization=$(get_kustomization "$model")
    if [ ! -f "$kustomization" ]; then
        log_error "Overlay not found: ${kustomization}"
        exit 1
    fi

    # Save selected overlay
    set_current_overlay "$model"

    # Show configuration
    log_info "Selected overlay: ${model}"
    case "$model" in
        tinyllama-1b)   echo "  Resources: single-gpu (1x g5.2xlarge)" ; echo "  Training:  qlora" ;;
        llama-7b)       echo "  Resources: single-gpu (1x g5.2xlarge)" ; echo "  Training:  qlora" ;;
        llama-13b)      echo "  Resources: multi-gpu (1x g5.12xlarge)" ; echo "  Training:  lora" ;;
        llama-70b)      echo "  Resources: multi-node (2x g5.12xlarge)" ; echo "  Training:  fsdp" ;;
    esac

    # Update settings in overlay if provided
    if [ -n "$settings" ]; then
        log_step "Enabling settings: ${settings}"
        update_settings_in_overlay "$model" "$settings"
        echo "  Settings:  ${settings}"
    else
        echo "  Settings:  (base defaults)"
    fi

    log_info "Configuration saved!"

    # Prompt for HuggingFace token if needed
    prompt_hf_token "$model"

    # Configure S3 storage from Terraform outputs
    configure_s3
}

# Update settings component in overlay kustomization
update_settings_in_overlay() {
    local model="$1"
    local settings="$2"
    local kustomization
    kustomization=$(get_kustomization "$model")

    local tmp_file
    tmp_file=$(mktemp)

    awk -v settings="$settings" '
    /components\/settings\// {
        if (/^  - /) { sub(/^  - /, "  # - ") }
        if ($0 ~ "settings/" settings) { sub(/^  # - /, "  - ") }
    }
    { print }
    ' "$kustomization" > "$tmp_file"

    mv "$tmp_file" "$kustomization"
}

# Show current configuration
show_config() {
    echo ""
    echo "Current configuration:"
    echo "======================"
    local tf_env="dev"
    if [ -f "$TF_ENV_FILE" ]; then
        tf_env=$(cat "$TF_ENV_FILE")
    fi
    echo "  Environment: ${tf_env}"

    local overlay
    overlay=$(get_current_overlay)
    local kustomization
    kustomization=$(get_kustomization "$overlay")

    echo "  Overlay:   ${overlay}"

    # Show optimal defaults based on overlay
    case "$overlay" in
        tinyllama-1b)
            echo "  Model:     TinyLlama/TinyLlama-1.1B-Chat-v1.0"
            echo "  Resources: single-gpu (1x g5.2xlarge)"
            echo "  Training:  qlora"
            ;;
        llama-7b)
            echo "  Model:     meta-llama/Llama-2-7b-hf"
            echo "  Resources: single-gpu (1x g5.2xlarge)"
            echo "  Training:  qlora"
            ;;
        llama-13b)
            echo "  Model:     meta-llama/Llama-2-13b-hf"
            echo "  Resources: multi-gpu (1x g5.12xlarge)"
            echo "  Training:  lora"
            ;;
        llama-70b)
            echo "  Model:     meta-llama/Llama-2-70b-hf"
            echo "  Resources: multi-node (2x g5.12xlarge)"
            echo "  Training:  fsdp"
            ;;
        *)
            log_error "Unknown overlay: ${overlay}"
            ;;
    esac

    # Extract settings from the overlay
    local settings=$(grep -E "^\s+-.*components/settings/" "$kustomization" 2>/dev/null | sed 's|.*/||')
    echo "  Settings:  ${settings:-base defaults}"

    # Get image from shared component (auto-generated by build-push)
    local image_component="${PROJECT_ROOT}/kubernetes/components/image/kustomization.yaml"
    local image=""
    if [ -f "$image_component" ]; then
        image=$(grep "newName:" "$image_component" 2>/dev/null | sed 's/.*newName: //')
    fi
    if [ -z "$image" ] || [[ "$image" == *"<"* ]]; then
        echo "  Image:     (not configured - run ./setup.sh build-push)"
    else
        echo "  Image:     ${image}"
    fi

    # Show S3 configuration
    show_s3_config

    echo ""
}

# ============================================================================
# Training Jobs
# ============================================================================

# Run training job
run_training() {
    log_info "Starting training job..."

    local overlay
    overlay=$(get_current_overlay)
    local kustomization
    kustomization=$(get_kustomization "$overlay")
    local overlay_dir="${OVERLAYS_DIR}/${overlay}"

    # Check if KubeRay operator is installed
    if ! kubectl get crd rayjobs.ray.io >/dev/null 2>&1; then
        log_error "KubeRay operator not installed. Run './setup.sh deploy' first."
        exit 1
    fi

    # Check if Kueue is installed for gang scheduling
    if kubectl get crd clusterqueues.kueue.x-k8s.io >/dev/null 2>&1; then
        log_info "Kueue detected - gang scheduling enabled"
    else
        log_warn "Kueue not detected - gang scheduling disabled"
    fi

    # Check if image is configured (file is auto-generated by build-push)
    local image_component="${PROJECT_ROOT}/kubernetes/components/image/kustomization.yaml"

    if [ ! -f "$image_component" ]; then
        log_error "Container image not configured!"
        echo ""
        echo "Run this command first:"
        echo "  ./setup.sh build-push    # Build and push image to ECR"
        echo ""
        echo "This will automatically generate: ${image_component}"
        exit 1
    fi

    # Also check for placeholder values (in case someone manually created the file)
    if grep -q "<ECR_REGISTRY>" "$image_component" 2>/dev/null; then
        log_error "Container image has placeholder values!"
        echo ""
        echo "Run this command to configure the image:"
        echo "  ./setup.sh build-push"
        exit 1
    fi

    # Show current config
    show_config

    # Delete previous RayJob if exists
    kubectl delete rayjob -n ml-training sft-training-ray --ignore-not-found 2>/dev/null || true

    # Apply the training resources
    log_step "Deploying training job from overlay: ${overlay}"
    kubectl apply -k "${overlay_dir}"

    log_info "Training job started!"
    echo ""
    echo "Monitor:"
    echo "  kubectl get rayjob -n ml-training -w"
    echo "  kubectl get pods -n ml-training -w"
    echo ""
    echo "Logs:"
    echo "  kubectl logs -f -n ml-training -l ray.io/node-type=head"
    echo ""
    echo "Stop:"
    echo "  ./setup.sh stop"
}

# Stop training job
stop_training() {
    log_info "Stopping training job..."
    kubectl delete rayjob -n ml-training sft-training-ray --ignore-not-found
    log_info "Training job stopped."
}

# ============================================================================
# S3 Storage Configuration
# ============================================================================
S3_COMPONENT="${PROJECT_ROOT}/kubernetes/components/storage/s3/kustomization.yaml"

configure_s3() {
    log_info "Configuring S3 storage from Terraform outputs..."

    cd "$(get_tf_env_dir)"

    # Check if Terraform has been applied
    if ! terraform output s3_bucket_name >/dev/null 2>&1; then
        log_error "Terraform outputs not found. Run './setup.sh deploy' first."
        exit 1
    fi

    # Get values from Terraform
    local bucket_name=$(terraform output -raw s3_bucket_name)
    local role_arn=$(terraform output -raw s3_training_role_arn)

    if [ -z "$bucket_name" ] || [ -z "$role_arn" ]; then
        log_error "Failed to get S3 configuration from Terraform."
        exit 1
    fi

    log_step "S3 Bucket: ${bucket_name}"
    log_step "IRSA Role: ${role_arn}"

    # Check if S3 component exists
    if [ ! -f "$S3_COMPONENT" ]; then
        log_error "S3 component not found: ${S3_COMPONENT}"
        exit 1
    fi

    # Update S3 component with actual values
    log_step "Updating S3 component..."

    # Replace placeholders in the S3 component
    sed -i.bak \
        -e "s|PLACEHOLDER_ROLE_ARN|${role_arn}|g" \
        -e "s|PLACEHOLDER_BUCKET|${bucket_name}|g" \
        "$S3_COMPONENT"
    rm -f "${S3_COMPONENT}.bak"

    log_info "S3 storage configured successfully!"
    echo ""
    echo "S3 Configuration:"
    echo "  Bucket:        ${bucket_name}"
    echo "  Role ARN:      ${role_arn}"
    echo "  Ray Storage:   s3://${bucket_name}/ray"
    echo "  Model Outputs: s3://${bucket_name}/outputs"
    echo ""
    echo "Checkpoints will be saved to S3 during training."
    echo "To verify, run: ./setup.sh show-config"
}

show_s3_config() {
    if [ ! -f "$S3_COMPONENT" ]; then
        log_warn "S3 component not found."
        return
    fi

    # Extract current values from the component
    local role_arn=$(grep "value:.*arn:aws" "$S3_COMPONENT" 2>/dev/null | head -1 | sed 's/.*value: "//' | sed 's/"$//')
    local bucket=$(grep "s3://" "$S3_COMPONENT" 2>/dev/null | head -1 | sed 's|.*s3://||' | sed 's|/.*||')

    if [ -n "$bucket" ] && [ "$bucket" != "PLACEHOLDER_BUCKET" ]; then
        echo "  S3 Bucket:     ${bucket}"
        echo "  Ray Storage:   s3://${bucket}/ray"
        echo "  Model Output:  s3://${bucket}/outputs"
    else
        echo "  S3 Storage:    (not configured - run ./setup.sh configure-s3)"
    fi
}

# ============================================================================
# Destroy Infrastructure
# ============================================================================
destroy_infrastructure() {
    log_warn "This will destroy all infrastructure!"
    read -p "Are you sure? (yes/no): " confirm

    if [ "$confirm" != "yes" ]; then
        log_info "Aborted."
        exit 0
    fi

    cd "$(get_tf_env_dir)"
    terraform destroy
}

# ============================================================================
# Usage
# ============================================================================
usage() {
    cat << EOF
Usage: $0 <command> [options]

Infrastructure:
  deploy              Deploy EKS + Karpenter + KubeRay + EFS + S3
  destroy             Destroy all infrastructure

Container Image:
  build               Build Docker image locally
  build-push          Build and push to ECR

Configuration:
  configure                         Interactive configuration
  configure <model> [settings]      Select model overlay with optional settings
  configure-s3                      Configure S3 storage from Terraform outputs
  set-env <env>                     Set Terraform environment (dev, existing-cluster)
  show-config                       Show current configuration

Training:
  train               Start training job
  stop                Stop training job

Other:
  check               Check prerequisites
  hf-token            Set up HuggingFace token (for gated models)
  hf-token delete     Delete HuggingFace token secret

Models (with optimal defaults):
  tinyllama-1b    [single-gpu + qlora]       Fast testing, no HF token needed
  llama-7b        [single-gpu + qlora]       Memory efficient 7B training
  llama-13b       [multi-gpu + lora]         Multi-GPU 13B training
  llama-70b       [multi-node + fsdp]        Distributed 70B training

Settings:
  quick-test      100 samples, 1 epoch (validation)
  full-training   All samples, 3 epochs (production)

Examples:
  # Full workflow
  $0 deploy                              # 1. Deploy infra
  $0 build-push                          # 2. Build image
  $0 configure tinyllama-1b quick-test   # 3. Configure
  $0 train                               # 4. Start training

  # Model selection examples
  $0 configure tinyllama-1b              # Use base defaults
  $0 configure tinyllama-1b quick-test   # Quick validation run
  $0 configure llama-7b full-training    # Full training run
  $0 configure llama-70b                 # 70B with FSDP defaults
EOF
}

# ============================================================================
# Main
# ============================================================================
main() {
    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        check)
            check_prerequisites
            ;;
        deploy)
            check_prerequisites
            deploy_infrastructure
            ;;
        build)
            build_image false
            ;;
        build-push)
            build_image true
            ;;
        configure)
            configure_training "$@"
            ;;
        configure-s3)
            configure_s3
            ;;
        show-config)
            show_config
            ;;
        train)
            run_training
            ;;
        stop)
            stop_training
            ;;
        destroy)
            destroy_infrastructure
            ;;
        set-env)
            if [ -z "${1:-}" ]; then
                log_error "Usage: $0 set-env <environment>"
                echo "Available: dev, existing-cluster"
                exit 1
            fi
            set_tf_env "$1"
            ;;
        hf-token)
            local action="${1:-}"
            if [ "$action" = "delete" ]; then
                delete_hf_token
            else
                echo ""
                echo "HuggingFace Token Setup"
                echo "======================="
                echo "Get your token at: https://huggingface.co/settings/tokens"
                echo ""
                if check_hf_secret; then
                    log_info "Token secret already exists."
                    read -p "Update token? (y/N): " update_token
                    if [ "$update_token" != "y" ] && [ "$update_token" != "Y" ]; then
                        exit 0
                    fi
                fi
                read -sp "Enter HuggingFace token: " hf_token
                echo ""
                if [ -n "$hf_token" ]; then
                    setup_hf_token "$hf_token"
                else
                    log_error "No token provided."
                    exit 1
                fi
            fi
            ;;
        help|--help|-h|*)
            usage
            ;;
    esac
}

main "$@"
