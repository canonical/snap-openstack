# Sunbeam Feature Functional Tests

Functional tests for Sunbeam feature enablement/disablement. These tests
connect to an **existing Sunbeam deployment** and run the enable/verify/disable
lifecycle for each feature, logging timing and basic behaviour checks.

The suite is designed to be run via `tox` from the `sunbeam-python` tree.

## Prerequisites

- **Existing Sunbeam deployment** already bootstrapped and reachable
- `sunbeam` CLI on `PATH` and configured to talk to that deployment
  - e.g. `sunbeam deployment list` shows your deployment
- `openstack` CLI configured for that cloud
  - e.g. `openstack endpoint list` works
- `juju` CLI installed and able to access the controller/model that backs the
  Sunbeam deployment

## Configuration

Create a config file from the example:

```bash
cd sunbeam-python
cp tests/functional/feature/test_config.yaml.example tests/functional/feature/test_config.yaml
```

Then edit `tests/functional/feature/test_config.yaml`:

```yaml
sunbeam:
  deployment_name: "ps6"        # Name shown by `sunbeam deployment list`

juju:
  model: "openstack"            # Juju model backing the cloud
  # controller: "my-controller" # Optional; auto-detected if omitted
```

### Run the full feature functional suite

```bash
tox -e functional-feature
```

### Run a single feature functional test

You can pass standard `pytest` selectors through tox via `posargs`. For example:

- **Instance Recovery**:

  ```bash
  tox -e functional-feature -- tests/functional/feature/test_features.py::test_instance_recovery
  ```

- **TLS CA**:

  ```bash
  tox -e functional-feature -- tests/functional/feature/test_features.py::test_tls_ca
  ```

## Feature coverage and dependencies

### Features in this suite

- **Enabled in current flow**
  - `instance-recovery`
  - `caas` (Containers as a Service)
  - `dns`
  - `images-sync`
  - `loadbalancer`
  - `resource-optimization`
  - `shared-filesystem`
  - `telemetry`
  - `observability`
  - `tls` (CA mode)
  - `vault`
  - `validation`
  - `secrets`

- **Present but intentionally disabled for now**
  - `baremetal`
  - `ldap`
  - `maintenance`
  - `pro`

### Feature dependencies

Some features have explicit dependencies:

- **CaaS (`caas`)**
  - Depends on: **`secrets`**, **`loadbalancer`**
  - The CaaS test ensures these dependencies are enabled before running.

- **Secrets as a Service (`secrets`)**
  - Depends on: **`vault`**
  - The Secrets test ensures the Vault feature is enabled before running.

- **TLS (Vault-backed)**
  - TLS can also be deployed in a Vault-backed mode which implicitly depends on
    the **`vault`** feature. This suite currently exercises only the TLS CA
    mode (`test_tls_ca`).

## Notes

- Disable failures are **logged and ignored** so that the suite continues
  to the next feature, matching the behaviour of the original tests.
