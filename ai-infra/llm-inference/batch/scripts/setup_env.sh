#!/usr/bin/env bash
# Create ./.venv, install dev + notebook extras, register the Jupyter kernel.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
KERNEL_NAME="llm-batch-deploy"

cd "${PROJECT_ROOT}"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Creating venv at ${VENV_DIR}..."
  python3.11 -m venv "${VENV_DIR}" || python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

pip install --upgrade pip
pip install -e '.[dev,notebook]'

# Register a Jupyter kernel spec that points at this venv.
python -m ipykernel install \
  --user \
  --name "${KERNEL_NAME}" \
  --display-name "Python (${KERNEL_NAME})"

echo ""
echo "Environment ready."
echo "  venv:   ${VENV_DIR}"
echo "  kernel: ${KERNEL_NAME}"
echo ""
echo "Next steps:"
echo "  source ${VENV_DIR}/bin/activate"
echo "  pytest tests/"
echo "  ./scripts/start_jupyter.sh"
