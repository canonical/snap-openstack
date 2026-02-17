# Chaos Mesh functional tests

Chaos Mesh-based resilience tests for Canonical OpenStack features.

This directory is separate from the feature functional tests under
`tests/functional/feature` so that chaos experiments can be run independently
and expanded over time.

## Prerequisites

- A working Canonical OpenStack deployment (same as feature functional tests).
- `sunbeam`, `openstack` and `juju` CLIs configured for that deployment.

Session-scoped fixtures automatically:

- Enable the **validation** feature once per run.
- Install or verify Chaos Mesh (Helm and `kubectl` are run via `juju exec` on
  `k8s/0` in the `openstack-machines` model).

## Run outcome and reports

Each chaos test run is **SUCCESS** or **FAIL**:

- **FAIL** if any unit does not return to `active` within the recovery timeout,
  or if the post-chaos **quick** validation test fails.
- **SUCCESS** only when all targeted units recover to `active` and the quick
  test passes.

A JSON report is written to `tests/functional/chaos/reports/` for each run.
Filenames include the outcome and a timestamp:

- `SUCCESS_<test_name>_<YYYY-MM-DD_HH-MM-SS>.json`
- `FAIL_<test_name>_<YYYY-MM-DD_HH-MM-SS>.json`

Reports include test duration, smoke test output/status, per-unit recovery
times and state sequences, and quick test output/status.

## Layout

- `validation/`: Chaos tests for the **validation** feature (Keystone, API pods,
  DB routers, infra).
- `reports/`: JSON reports from each run.

## Running the tests

From the `sunbeam-python` tree:

```bash
tox -e functional-chaos
```

Or run a single test:

```bash
python -m pytest -s -vv tests/functional/chaos/validation/test_validation_keystone_chaos.py --config tests/functional/feature/test_config.yaml
```

