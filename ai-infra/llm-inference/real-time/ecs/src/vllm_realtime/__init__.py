"""vllm_realtime — thin Python helper for the real-time/ecs stacks.

Glue around the four CFN templates in ``real-time/ecs/cfn/``:

* ``00-networking.yaml``      (one per region)
* ``10-cluster.yaml``         (one per region)
* ``20-vllm-model.yaml``      (one per model)
* ``30-litellm-router.yaml``  (one per model)

Plus a few utilities:

* :class:`ModelService`       — per-model dataclass holding the deploy params.
* :func:`deploy_stack`        — deploy or update a CFN stack and wait.
* :func:`teardown_stack`      — delete a CFN stack and wait.
* :func:`upsert_secret`       — idempotent SecretsManager upsert.
* :func:`smoke_test_endpoint` — single chat-completion request through the ALB.
* :func:`async_load_test`     — N concurrent chat-completion requests.
"""
from __future__ import annotations

from .deployer import (
    CFN_DIR,
    ModelService,
    deploy_stack,
    teardown_stack,
    upsert_secret,
    wait_for_alb_healthy,
)
from .load_test import async_load_test, smoke_test_endpoint

__all__ = [
    "CFN_DIR",
    "ModelService",
    "async_load_test",
    "deploy_stack",
    "smoke_test_endpoint",
    "teardown_stack",
    "upsert_secret",
    "wait_for_alb_healthy",
]
