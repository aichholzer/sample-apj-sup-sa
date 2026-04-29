# Multi-GPU PyTorch Training Container for EKS
# Based on NVIDIA PyTorch container with CUDA support
#
# Supports GPU architectures:
# - sm_80: A100 (Ampere)
# - sm_86: A10G (Ampere)
# - sm_89: L40S, RTX 4090 (Ada Lovelace)
# - sm_90: H100 (Hopper)
# - sm_120: RTX 6000 Blackwell, B100/B200 (Blackwell)

ARG CUDA_VERSION=12.8.1
ARG PYTORCH_VERSION=2.8.0

FROM nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu22.04 AS base

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    libaio-dev \
    libffi-dev \
    libssl-dev \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Create symbolic links for python
RUN ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip "setuptools<78.0" wheel

# ============================================================================
# Builder stage - install Python packages
# ============================================================================
FROM base AS builder

WORKDIR /build

# Install PyTorch with CUDA 12.8 support
# Using cu128 index for Blackwell + Flash Attention compatibility
ARG PYTORCH_VERSION
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cu128 \
    torch==${PYTORCH_VERSION} torchvision torchaudio

# Copy and install requirements
COPY src/training/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Ray with train support for multi-node distributed training
# Using latest Ray version for compatibility with modern setuptools
RUN pip install --no-cache-dir \
    "ray[train]>=2.40" \
    "ray[default]>=2.40"

# Re-install PyTorch packages to ensure version consistency
# (requirements.txt may have upgraded torch, breaking torchvision compatibility)
ARG PYTORCH_VERSION
RUN pip install --no-cache-dir --force-reinstall \
    --index-url https://download.pytorch.org/whl/cu128 \
    torch==${PYTORCH_VERSION} torchvision torchaudio

# Flash Attention 2 - install from pre-built wheel
# Supports: sm_80 (A100), sm_86 (A10G), sm_89 (L40S), sm_90 (H100)
# Note: sm_120 (Blackwell) not yet supported - will fall back to SDPA
RUN pip install --no-cache-dir ninja packaging einops

# Install flash-attn from pre-built wheel (much faster than building from source)
# Using torch2.8 wheel matching our PyTorch version
RUN pip install --no-cache-dir "flash_attn @ https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.8cxx11abiTRUE-cp310-cp310-linux_x86_64.whl"

# ============================================================================
# Runtime stage
# ============================================================================
FROM base AS runtime

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set up working directory
WORKDIR /app

# Copy training code
COPY src/training/ /app/

# Environment variables for PyTorch/NCCL
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# GPU architecture support: Ampere (8.0, 8.6), Ada (8.9), Hopper (9.0), Blackwell (12.0)
ENV TORCH_CUDA_ARCH_LIST="8.0 8.6 8.9 9.0 12.0"
ENV NCCL_DEBUG=INFO
ENV NCCL_IB_DISABLE=1

# Create directories for data and output
RUN mkdir -p /data /output

# Set default command
ENTRYPOINT ["python", "-u", "train.py"]
CMD []

# ============================================================================
# Development stage with additional tools
# ============================================================================
FROM runtime AS development

# Install development tools
RUN pip install --no-cache-dir \
    ipython \
    jupyter \
    pytest \
    black \
    isort \
    flake8

# Enable SSH for debugging (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    vim \
    htop \
    nvtop \
    && rm -rf /var/lib/apt/lists/*

CMD ["/bin/bash"]
