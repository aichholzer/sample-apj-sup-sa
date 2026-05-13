# GPU Smoke

OSMO-submitted GPU smoke workflow. It requests the AWS G7e OSMO platform and runs `nvidia-smi`.

OSMO validates workflow resources against currently visible backend capacity, so prewarm a G7e node first:

```bash
GPU_PREWARM_INSTANCE_TYPE=g7e.2xlarge infra/kubernetes/prewarm-gpu-node.sh
SMOKE_SET_NGC_CREDENTIAL=true \
  WORKFLOW_FILE=examples/smoke/gpu-workflow/workflow.yaml \
  SMOKE_TIMEOUT_ATTEMPTS=180 \
  examples/run-workflow.sh
infra/kubernetes/wait-gpu-node-cleanup.sh
```

Expected result: the workflow completes after running `nvidia-smi` on a G7e GPU node.
