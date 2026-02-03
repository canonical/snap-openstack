# Chaos Mesh functional tests

Chaos Mesh-based resilience tests for Canonical OpenStack features.

This directory is intentionally separate from the standard feature functional
tests under `tests/functional/feature` so that chaos experiments can be run
independently and expanded over time.

## Prerequisites

- A working Canonical OpenStack deployment (same requirements as the feature
  functional tests).
- `sunbeam`, `openstack` and `juju` CLIs configured for that deployment.
- `kubectl` configured to talk to the Kubernetes cluster that backs the
  OpenStack model.
- Chaos Mesh installed and running, typically in the `chaos-mesh` namespace.

## Layout

- `validation/`: Chaos tests that target the **validation** feature.

Additional feature-specific chaos tests can be added as new subdirectories
alongside `validation/`.

## Running the chaos tests

From the `sunbeam-python` tree:

```bash
tox -e functional-chaos
```

You can also run individual chaos tests via `pytest`, for example:

```bash
python -m pytest -s -vv tests/functional/chaos/validation/test_validation_keystone_chaos.py
```

