#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXAMPLE_DIR="${ROOT_DIR}/benchmarks/g7e-efa-ddp"

# shellcheck source=../../scripts/common.sh
# shellcheck disable=SC1091
source "${ROOT_DIR}/scripts/common.sh"

require_cmds kubectl python3

KUBE_CONTEXT="${KUBE_CONTEXT:-}"
NAMESPACE="${NAMESPACE:-osmo-workflows}"
IMAGE="${IMAGE:-$(version_value pytorch_training_image)}"
BENCH_NAME="${BENCH_NAME:-g7e-efa-ddp-benchmark}"
MODES="${MODES:-efa socket}"
read -r -a MODES_ARRAY <<<"${MODES}"
PARAM_MIB="${PARAM_MIB:-256}"
WARMUP_STEPS="${WARMUP_STEPS:-2}"
STEPS="${STEPS:-12}"
BUCKET_CAP_MB="${BUCKET_CAP_MB:-64}"
TIMEOUT="${TIMEOUT:-20m}"
VALIDATION_DIR="${VALIDATION_DIR:-${EXAMPLE_DIR}/validation}"
KEEP_RESOURCES="${KEEP_RESOURCES:-false}"

[[ -n "${KUBE_CONTEXT}" ]] || die "KUBE_CONTEXT is required"

mkdir -p "${VALIDATION_DIR}"
kubectl_ctx=(kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}")
"${kubectl_ctx[@]}" create namespace "${NAMESPACE}" >/dev/null 2>&1 || true

cleanup_mode() {
  local mode="$1"
  local run_name="${BENCH_NAME}-${mode}"
  if [[ "${KEEP_RESOURCES}" != "true" ]]; then
    "${kubectl_ctx[@]}" delete pod "${run_name}-master" "${run_name}-worker" \
      --ignore-not-found=true --wait=true --timeout=2m >/dev/null || true
    "${kubectl_ctx[@]}" delete service "${run_name}-master" --ignore-not-found=true >/dev/null || true
    "${kubectl_ctx[@]}" delete configmap "${run_name}-script" --ignore-not-found=true >/dev/null || true
  fi
}

mode_env() {
  local mode="$1"
  if [[ "${mode}" == "efa" ]]; then
    cat <<'YAML'
        - name: FI_PROVIDER
          value: efa
        - name: FI_EFA_USE_DEVICE_RDMA
          value: "1"
YAML
  else
    cat <<'YAML'
        - name: NCCL_NET
          value: Socket
        - name: NCCL_SOCKET_IFNAME
          value: eth0
YAML
  fi
}

mode_resource() {
  if [[ "$1" == "efa" ]]; then
    cat <<'YAML'
          vpc.amazonaws.com/efa: "1"
YAML
  fi
}

render_template() {
  python3 "${ROOT_DIR}/scripts/render_template.py" "$1"
}

apply_mode() {
  local mode="$1"
  local run_name="${BENCH_NAME}-${mode}"

  cleanup_mode "${mode}"
  "${kubectl_ctx[@]}" create configmap "${run_name}-script" \
    --from-file=train.py="${EXAMPLE_DIR}/train.py" \
    --dry-run=client -o yaml | "${kubectl_ctx[@]}" apply -f - >/dev/null

  RUN_NAME="${run_name}" \
  MODE="${mode}" \
  MODE_ENV="$(mode_env "${mode}")" \
  MODE_RESOURCE="$(mode_resource "${mode}")" \
  IMAGE="${IMAGE}" \
  PARAM_MIB="${PARAM_MIB}" \
  WARMUP_STEPS="${WARMUP_STEPS}" \
  STEPS="${STEPS}" \
  BUCKET_CAP_MB="${BUCKET_CAP_MB}" \
    render_template "${EXAMPLE_DIR}/templates/benchmark.yaml" | "${kubectl_ctx[@]}" apply -f -
}

collect_mode() {
  local mode="$1"
  local run_name="${BENCH_NAME}-${mode}"

  if ! "${kubectl_ctx[@]}" wait --for=condition=Ready \
    "pod/${run_name}-master" "pod/${run_name}-worker" --timeout="${TIMEOUT}"; then
    "${kubectl_ctx[@]}" describe pod "${run_name}-master" "${run_name}-worker" \
      >"${VALIDATION_DIR}/${mode}-describe.txt" || true
    "${kubectl_ctx[@]}" logs "${run_name}-master" >"${VALIDATION_DIR}/${mode}-master.log" || true
    "${kubectl_ctx[@]}" logs "${run_name}-worker" >"${VALIDATION_DIR}/${mode}-worker.log" || true
    exit 1
  fi

  if ! "${kubectl_ctx[@]}" wait --for=jsonpath='{.status.phase}'=Succeeded \
    "pod/${run_name}-master" "pod/${run_name}-worker" --timeout="${TIMEOUT}"; then
    "${kubectl_ctx[@]}" describe pod "${run_name}-master" "${run_name}-worker" \
      >"${VALIDATION_DIR}/${mode}-describe.txt" || true
    "${kubectl_ctx[@]}" logs "${run_name}-master" >"${VALIDATION_DIR}/${mode}-master.log" || true
    "${kubectl_ctx[@]}" logs "${run_name}-worker" >"${VALIDATION_DIR}/${mode}-worker.log" || true
    exit 1
  fi

  "${kubectl_ctx[@]}" logs "${run_name}-master" >"${VALIDATION_DIR}/${mode}-master.log"
  "${kubectl_ctx[@]}" logs "${run_name}-worker" >"${VALIDATION_DIR}/${mode}-worker.log"
  cleanup_mode "${mode}"
}

for mode in "${MODES_ARRAY[@]}"; do
  [[ "${mode}" == "efa" || "${mode}" == "socket" ]] || die "unsupported mode: ${mode}"
  log "running ${mode} DDP benchmark"
  apply_mode "${mode}"
  collect_mode "${mode}"
done

echo "Validation logs written to ${VALIDATION_DIR}"
