#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXAMPLE_DIR="${ROOT_DIR}/benchmarks/g7e-efa-nccl"

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "${ROOT_DIR}/scripts/common.sh"

require_cmds kubectl python3 ssh-keygen

KUBE_CONTEXT="${KUBE_CONTEXT:-}"
NAMESPACE="${NAMESPACE:-osmo-workflows}"
IMAGE="${IMAGE:-$(version_value nccl_benchmark_image)}"
NCCL_VERSION="${NCCL_VERSION:-$(version_value nccl_benchmark_nccl_version)}"
BENCH_NAME="${BENCH_NAME:-nccl-efa-benchmark}"
WORKER_POD="${WORKER_POD:-nccl-efa-worker}"
MASTER_POD="${MASTER_POD:-nccl-efa-master}"
SSH_SECRET="${SSH_SECRET:-nccl-bench-ssh}"
KEEP_RESOURCES="${KEEP_RESOURCES:-false}"

[[ -n "${KUBE_CONTEXT}" ]] || die "KUBE_CONTEXT is required"

tmp_dir="$(mktemp -d)"
kubectl_ctx=(kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}")

cleanup() {
  if [[ "${KEEP_RESOURCES}" != "true" ]]; then
    "${kubectl_ctx[@]}" delete pod "${MASTER_POD}" "${WORKER_POD}" \
      --ignore-not-found=true --wait=true --timeout=2m >/dev/null || true
    "${kubectl_ctx[@]}" delete service "${WORKER_POD}" --ignore-not-found=true >/dev/null || true
    "${kubectl_ctx[@]}" delete secret "${SSH_SECRET}" --ignore-not-found=true >/dev/null || true
  fi
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

render_template() {
  python3 "${ROOT_DIR}/scripts/render_template.py" "$1"
}

"${kubectl_ctx[@]}" create namespace "${NAMESPACE}" >/dev/null 2>&1 || true
ssh-keygen -q -t ed25519 -N "" -f "${tmp_dir}/id_ed25519"
"${kubectl_ctx[@]}" delete secret "${SSH_SECRET}" --ignore-not-found=true >/dev/null
"${kubectl_ctx[@]}" create secret generic "${SSH_SECRET}" \
  --from-file=id_ed25519="${tmp_dir}/id_ed25519" \
  --from-file=authorized_keys="${tmp_dir}/id_ed25519.pub" >/dev/null

IMAGE="${IMAGE}" \
NCCL_VERSION="${NCCL_VERSION}" \
BENCH_NAME="${BENCH_NAME}" \
WORKER_POD="${WORKER_POD}" \
MASTER_POD="${MASTER_POD}" \
SSH_SECRET="${SSH_SECRET}" \
  render_template "${EXAMPLE_DIR}/templates/benchmark.yaml" | "${kubectl_ctx[@]}" apply -f -

"${kubectl_ctx[@]}" wait --for=condition=Ready "pod/${WORKER_POD}" "pod/${MASTER_POD}" --timeout=10m
"${kubectl_ctx[@]}" logs -f "${MASTER_POD}"

cleanup
