#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
EXAMPLE_DIR="${ROOT_DIR}/examples/policy-training/gr00t-so100-efa-multinode-finetune"

# shellcheck source=../../../scripts/common.sh
# shellcheck disable=SC1091
source "${ROOT_DIR}/scripts/common.sh"

require_cmds kubectl python3

KUBE_CONTEXT="${KUBE_CONTEXT:-}"
NAMESPACE="${NAMESPACE:-osmo-workflows}"
RUN_NAME="${RUN_NAME:-gr00t-k8s-efa-multinode}"
IMAGE="${IMAGE:-$(version_value gr00t_efa_training_image)}"
GR00T_REPOSITORY="${GR00T_REPOSITORY:-$(version_value gr00t_repository)}"
GR00T_REF="${GR00T_REF:-$(version_value gr00t_ref)}"
BASE_MODEL_PATH="${BASE_MODEL_PATH:-nvidia/GR00T-N1.6-3B}"
DATASET_PATH="${DATASET_PATH:-demo_data/cube_to_bowl_5}"
MODALITY_CONFIG_PATH="${MODALITY_CONFIG_PATH:-examples/SO100/so100_config.py}"
EMBODIMENT_TAG="${EMBODIMENT_TAG:-NEW_EMBODIMENT}"
MAX_STEPS="${MAX_STEPS:-2}"
SAVE_STEPS="${SAVE_STEPS:-2}"
SAVE_TOTAL_LIMIT="${SAVE_TOTAL_LIMIT:-1}"
CHECKPOINT_NAME="${CHECKPOINT_NAME:-checkpoint-${MAX_STEPS}}"
GLOBAL_BATCH_SIZE="${GLOBAL_BATCH_SIZE:-2}"
DATALOADER_NUM_WORKERS="${DATALOADER_NUM_WORKERS:-0}"
LEARNING_RATE="${LEARNING_RATE:-1e-4}"
SHARD_SIZE="${SHARD_SIZE:-1024}"
EPISODE_SAMPLING_RATE="${EPISODE_SAMPLING_RATE:-0.1}"
NUM_SHARDS_PER_EPOCH="${NUM_SHARDS_PER_EPOCH:-100000}"
GPU_METRICS_INTERVAL_SECONDS="${GPU_METRICS_INTERVAL_SECONDS:-10}"
TIMEOUT="${TIMEOUT:-120m}"
ARTIFACT_WAIT_SECONDS="${ARTIFACT_WAIT_SECONDS:-7200}"
VALIDATION_DIR="${VALIDATION_DIR:-${EXAMPLE_DIR}/validation}"
KEEP_RESOURCES="${KEEP_RESOURCES:-false}"
COPY_CHECKPOINT="${COPY_CHECKPOINT:-true}"
IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET:-}"
HF_TOKEN="${HF_TOKEN:-}"
HF_TOKEN_FILE="${HF_TOKEN_FILE:-${HOME}/.huggingface/token}"
NODE_SELECTOR_KEY="${NODE_SELECTOR_KEY:-aws.osmo.reference/nodepool}"
NODE_SELECTOR_VALUE="${NODE_SELECTOR_VALUE:-g6e}"
CPU_REQUEST="${CPU_REQUEST:-8}"
MEMORY_REQUEST="${MEMORY_REQUEST:-64Gi}"
EPHEMERAL_STORAGE_REQUEST="${EPHEMERAL_STORAGE_REQUEST:-}"
EFA_HUGEPAGES_2MI="${EFA_HUGEPAGES_2MI:-}"
REQUIRE_DISTINCT_NODES="${REQUIRE_DISTINCT_NODES:-true}"
FI_EFA_USE_DEVICE_RDMA="${FI_EFA_USE_DEVICE_RDMA:-1}"
NCCL_NET="${NCCL_NET:-}"
NCCL_NET_GDR_LEVEL="${NCCL_NET_GDR_LEVEL:-}"

WORLD_SIZE="${WORLD_SIZE:-2}"
GPUS_PER_NODE=1
MASTER_PORT=29500
MASTER_POD="${RUN_NAME}-master"
WORKER_POD="${RUN_NAME}-worker"
CONFIGMAP_NAME="${RUN_NAME}-scripts"
SECRET_NAME="${RUN_NAME}-hf-token"

[[ -n "${KUBE_CONTEXT}" ]] || die "KUBE_CONTEXT is required"
if [[ -z "${NODE_SELECTOR_KEY}" || -z "${NODE_SELECTOR_VALUE}" ]]; then
  die "NODE_SELECTOR_KEY and NODE_SELECTOR_VALUE must be non-empty"
fi
if [[ "${WORLD_SIZE}" != "1" && "${WORLD_SIZE}" != "2" ]]; then
  die "WORLD_SIZE must be 1 or 2"
fi

if [[ -z "${HF_TOKEN}" && -f "${HF_TOKEN_FILE}" ]]; then
  HF_TOKEN="$(tr -d '\n\r' <"${HF_TOKEN_FILE}")"
fi
if [[ -z "${HF_TOKEN}" ]]; then
  die "HF_TOKEN or HF_TOKEN_FILE is required for GR00T model download"
fi

mkdir -p "${VALIDATION_DIR}"
kubectl_ctx=(kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}")
"${kubectl_ctx[@]}" create namespace "${NAMESPACE}" >/dev/null 2>&1 || true

cleanup() {
  if [[ "${KEEP_RESOURCES}" != "true" ]]; then
    "${kubectl_ctx[@]}" delete pod "${MASTER_POD}" "${WORKER_POD}" --ignore-not-found=true --wait=true --timeout=2m >/dev/null || true
    "${kubectl_ctx[@]}" delete service "${MASTER_POD}" --ignore-not-found=true >/dev/null || true
    "${kubectl_ctx[@]}" delete configmap "${CONFIGMAP_NAME}" --ignore-not-found=true >/dev/null || true
    "${kubectl_ctx[@]}" delete secret "${SECRET_NAME}" --ignore-not-found=true >/dev/null || true
  fi
}
trap cleanup EXIT

image_pull_secrets_yaml() {
  if [[ -n "${IMAGE_PULL_SECRET}" ]]; then
    cat <<YAML
  imagePullSecrets:
    - name: ${IMAGE_PULL_SECRET}
YAML
  fi
}

efa_resource_yaml() {
  cat <<'YAML'
          vpc.amazonaws.com/efa: "1"
YAML
  if [[ -n "${EFA_HUGEPAGES_2MI}" ]]; then
    printf '          hugepages-2Mi: %s\n' "${EFA_HUGEPAGES_2MI}"
  fi
}

ephemeral_storage_yaml() {
  if [[ -n "${EPHEMERAL_STORAGE_REQUEST}" ]]; then
    printf '          ephemeral-storage: %s\n' "${EPHEMERAL_STORAGE_REQUEST}"
  fi
}

pod_anti_affinity_yaml() {
  if [[ "${REQUIRE_DISTINCT_NODES}" == "true" ]]; then
    cat <<YAML
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchLabels:
              app.kubernetes.io/name: ${RUN_NAME}
          topologyKey: kubernetes.io/hostname
YAML
  fi
}

optional_nccl_env_yaml() {
  if [[ -n "${NCCL_NET}" ]]; then
    cat <<YAML
        - name: NCCL_NET
          value: "${NCCL_NET}"
YAML
  fi
  if [[ -n "${NCCL_NET_GDR_LEVEL}" ]]; then
    cat <<YAML
        - name: NCCL_NET_GDR_LEVEL
          value: "${NCCL_NET_GDR_LEVEL}"
YAML
  fi
}

render_worker_pod() {
  [[ "${WORLD_SIZE}" == "2" ]] || return 0
  ROLE=worker POD_NAME="${WORKER_POD}" NODE_RANK=1 render_pod
}

render_template() {
  python3 "${ROOT_DIR}/scripts/render_template.py" "$1"
}

render_service() {
  MASTER_POD="${MASTER_POD}" \
  RUN_NAME="${RUN_NAME}" \
  MASTER_PORT="${MASTER_PORT}" \
    render_template "${EXAMPLE_DIR}/templates/service.yaml"
}

render_pod() {
  ROLE="${ROLE}" \
  POD_NAME="${POD_NAME}" \
  NODE_RANK="${NODE_RANK}" \
  RUN_NAME="${RUN_NAME}" \
  IMAGE="${IMAGE}" \
  GR00T_REPOSITORY="${GR00T_REPOSITORY}" \
  GR00T_REF="${GR00T_REF}" \
  BASE_MODEL_PATH="${BASE_MODEL_PATH}" \
  DATASET_PATH="${DATASET_PATH}" \
  MODALITY_CONFIG_PATH="${MODALITY_CONFIG_PATH}" \
  EMBODIMENT_TAG="${EMBODIMENT_TAG}" \
  MAX_STEPS="${MAX_STEPS}" \
  SAVE_STEPS="${SAVE_STEPS}" \
  SAVE_TOTAL_LIMIT="${SAVE_TOTAL_LIMIT}" \
  GLOBAL_BATCH_SIZE="${GLOBAL_BATCH_SIZE}" \
  DATALOADER_NUM_WORKERS="${DATALOADER_NUM_WORKERS}" \
  LEARNING_RATE="${LEARNING_RATE}" \
  SHARD_SIZE="${SHARD_SIZE}" \
  EPISODE_SAMPLING_RATE="${EPISODE_SAMPLING_RATE}" \
  NUM_SHARDS_PER_EPOCH="${NUM_SHARDS_PER_EPOCH}" \
  GPU_METRICS_INTERVAL_SECONDS="${GPU_METRICS_INTERVAL_SECONDS}" \
  NODE_SELECTOR_KEY="${NODE_SELECTOR_KEY}" \
  NODE_SELECTOR_VALUE="${NODE_SELECTOR_VALUE}" \
  FI_EFA_USE_DEVICE_RDMA="${FI_EFA_USE_DEVICE_RDMA}" \
  SECRET_NAME="${SECRET_NAME}" \
  WORLD_SIZE="${WORLD_SIZE}" \
  GPUS_PER_NODE="${GPUS_PER_NODE}" \
  MASTER_POD="${MASTER_POD}" \
  MASTER_PORT="${MASTER_PORT}" \
  CONFIGMAP_NAME="${CONFIGMAP_NAME}" \
  CPU_REQUEST="${CPU_REQUEST}" \
  MEMORY_REQUEST="${MEMORY_REQUEST}" \
  IMAGE_PULL_SECRETS="$(image_pull_secrets_yaml)" \
  POD_ANTI_AFFINITY="$(pod_anti_affinity_yaml)" \
  OPTIONAL_NCCL_ENV="$(optional_nccl_env_yaml)" \
  EPHEMERAL_STORAGE="$(ephemeral_storage_yaml)" \
  EFA_RESOURCE="$(efa_resource_yaml)" \
    render_template "${EXAMPLE_DIR}/templates/pod.yaml"
}

cleanup

"${kubectl_ctx[@]}" create secret generic "${SECRET_NAME}" \
  --from-literal=token="${HF_TOKEN}" \
  --dry-run=client -o yaml | "${kubectl_ctx[@]}" apply -f - >/dev/null

"${kubectl_ctx[@]}" create configmap "${CONFIGMAP_NAME}" \
  --from-file=entry.sh="${EXAMPLE_DIR}/entry.sh" \
  --from-file=isaac-gr00t-video-backend-env.patch="${EXAMPLE_DIR}/patches/isaac-gr00t-video-backend-env.patch" \
  --from-file=isaac-gr00t-video-indices-pyav-fallback.patch="${EXAMPLE_DIR}/patches/isaac-gr00t-video-indices-pyav-fallback.patch" \
  --from-file=isaac-gr00t-phase-timing.patch="${EXAMPLE_DIR}/patches/isaac-gr00t-phase-timing.patch" \
  --dry-run=client -o yaml | "${kubectl_ctx[@]}" apply -f - >/dev/null

render_service | "${kubectl_ctx[@]}" apply -f -
ROLE=master POD_NAME="${MASTER_POD}" NODE_RANK=0 render_pod | "${kubectl_ctx[@]}" apply -f -
if [[ "${WORLD_SIZE}" == "2" ]]; then
  render_worker_pod | "${kubectl_ctx[@]}" apply -f -
fi

POD_ARGS=("pod/${MASTER_POD}")
if [[ "${WORLD_SIZE}" == "2" ]]; then
  POD_ARGS+=("pod/${WORKER_POD}")
fi

snapshot_runtime_state() {
  "${kubectl_ctx[@]}" get "${POD_ARGS[@]}" -o wide >"${VALIDATION_DIR}/pods.txt" || true
  kubectl --context "${KUBE_CONTEXT}" get nodeclaims >"${VALIDATION_DIR}/nodeclaims.txt" || true
}

if ! "${kubectl_ctx[@]}" wait --for=condition=Ready "${POD_ARGS[@]}" --timeout="${TIMEOUT}"; then
  snapshot_runtime_state
  "${kubectl_ctx[@]}" describe "${POD_ARGS[@]}" >"${VALIDATION_DIR}/describe.txt" || true
  "${kubectl_ctx[@]}" logs "${MASTER_POD}" >"${VALIDATION_DIR}/master.log" || true
  if [[ "${WORLD_SIZE}" == "2" ]]; then
    "${kubectl_ctx[@]}" logs "${WORKER_POD}" >"${VALIDATION_DIR}/worker.log" || true
  fi
  exit 1
fi
snapshot_runtime_state

wait_for_artifact_markers() {
  local deadline=$((SECONDS + ARTIFACT_WAIT_SECONDS))
  local phase

  while (( SECONDS < deadline )); do
    phase="$("${kubectl_ctx[@]}" get pod "${MASTER_POD}" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    [[ "${phase}" != "Failed" ]] || return 1
    if [[ "${WORLD_SIZE}" == "2" ]]; then
      phase="$("${kubectl_ctx[@]}" get pod "${WORKER_POD}" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
      [[ "${phase}" != "Failed" ]] || return 1
    fi

    if "${kubectl_ctx[@]}" exec "${MASTER_POD}" -c train -- test -f /tmp/gr00t-output/rank-0/train-ok >/dev/null 2>&1; then
      if [[ "${WORLD_SIZE}" == "1" ]] \
        || "${kubectl_ctx[@]}" exec "${WORKER_POD}" -c train -- test -f /tmp/gr00t-output/rank-1/train-ok >/dev/null 2>&1; then
        return 0
      fi
    fi
    sleep 10
  done
  return 1
}

if ! wait_for_artifact_markers; then
  snapshot_runtime_state
  "${kubectl_ctx[@]}" describe "${POD_ARGS[@]}" >"${VALIDATION_DIR}/describe.txt" || true
  "${kubectl_ctx[@]}" logs "${MASTER_POD}" >"${VALIDATION_DIR}/master.log" || true
  if [[ "${WORLD_SIZE}" == "2" ]]; then
    "${kubectl_ctx[@]}" logs "${WORKER_POD}" >"${VALIDATION_DIR}/worker.log" || true
  fi
  exit 1
fi

"${kubectl_ctx[@]}" logs "${MASTER_POD}" >"${VALIDATION_DIR}/master.log"
if [[ "${WORLD_SIZE}" == "2" ]]; then
  "${kubectl_ctx[@]}" logs "${WORKER_POD}" >"${VALIDATION_DIR}/worker.log"
fi
rm -rf "${VALIDATION_DIR}/master-output" "${VALIDATION_DIR}/worker-output"
mkdir -p "${VALIDATION_DIR}/master-output/${RUN_NAME}"
if [[ "${WORLD_SIZE}" == "2" ]]; then
  mkdir -p "${VALIDATION_DIR}/worker-output/${RUN_NAME}"
fi
"${kubectl_ctx[@]}" cp --retries=3 "${MASTER_POD}:/tmp/gr00t-output/rank-0" "${VALIDATION_DIR}/master-output/rank-0" -c train >/dev/null
if [[ "${WORLD_SIZE}" == "2" ]]; then
  "${kubectl_ctx[@]}" cp --retries=3 "${WORKER_POD}:/tmp/gr00t-output/rank-1" "${VALIDATION_DIR}/worker-output/rank-1" -c train >/dev/null
fi
if [[ "${COPY_CHECKPOINT}" == "true" ]]; then
  "${kubectl_ctx[@]}" cp --retries=3 \
    "${MASTER_POD}:/tmp/gr00t-output/${RUN_NAME}/${CHECKPOINT_NAME}" \
    "${VALIDATION_DIR}/master-output/${RUN_NAME}/${CHECKPOINT_NAME}" -c train >/dev/null
  if [[ "${WORLD_SIZE}" == "2" ]]; then
    "${kubectl_ctx[@]}" cp --retries=3 \
      "${WORKER_POD}:/tmp/gr00t-output/${RUN_NAME}/${CHECKPOINT_NAME}" \
      "${VALIDATION_DIR}/worker-output/${RUN_NAME}/${CHECKPOINT_NAME}" -c train >/dev/null
  fi
fi
"${kubectl_ctx[@]}" exec "${MASTER_POD}" -c train -- cat /tmp/gr00t-output/run-manifest-rank-0.json \
  >"${VALIDATION_DIR}/master-output/run-manifest-rank-0.json"
if [[ "${WORLD_SIZE}" == "2" ]]; then
  "${kubectl_ctx[@]}" exec "${WORKER_POD}" -c train -- cat /tmp/gr00t-output/run-manifest-rank-1.json \
    >"${VALIDATION_DIR}/worker-output/run-manifest-rank-1.json"
fi
"${kubectl_ctx[@]}" exec "${MASTER_POD}" -c train -- touch /tmp/gr00t-output/rank-0/release >/dev/null || true
if [[ "${WORLD_SIZE}" == "2" ]]; then
  "${kubectl_ctx[@]}" exec "${WORKER_POD}" -c train -- touch /tmp/gr00t-output/rank-1/release >/dev/null || true
fi
"${kubectl_ctx[@]}" wait --for=jsonpath='{.status.phase}'=Succeeded "${POD_ARGS[@]}" --timeout=5m >/dev/null || true
cleanup
echo "Validation artifacts written to ${VALIDATION_DIR}"
