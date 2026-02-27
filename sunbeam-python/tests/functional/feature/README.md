# Sunbeam Feature Functional Tests

Functional tests for Sunbeam feature enablement/disablement. These tests
connect to an **existing Sunbeam deployment** and run the enable/verify
lifecycle for each feature, with an optional disable phase controlled by
configuration.

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

- **Vault**:

  ```bash
  tox -e functional-feature -- tests/functional/feature/test_features.py::test_vault
  ```

You can also run any single feature test **directly with the virtualenv Python**,
which is handy when you are iterating locally:

```bash
../.venv/bin/python -m pytest \
  tests/functional/feature/test_features.py::test_<feature_name> \
  --config tests/functional/feature/test_config.yaml
```

For example:

- TLS CA:

  ```bash
  ../.venv/bin/python -m pytest \
    tests/functional/feature/test_features.py::test_tls_ca \
    --config tests/functional/feature/test_config.yaml
  ```

- Vault:

  ```bash
  ../.venv/bin/python -m pytest \
    tests/functional/feature/test_features.py::test_vault \
    --config tests/functional/feature/test_config.yaml
  ```

### Control whether features are disabled after tests

By default, features are **left enabled** after their tests complete. You can
enable the legacy "enable then disable" behaviour via `test_config.yaml`:

```yaml
features:
  disable_after: true          # disable every feature after its test
```

You can also override this per feature:

```yaml
features:
  disable_after: false         # default for all features

  tls:
    disable_after: true        # only TLS is disabled after test

  vault:
    disable_after: false       # explicitly keep Vault enabled

You can also override this behaviour **from the command line** without editing
the config file, using the `--features-disable-after` pytest option. When
running via `tox`:

```bash
tox -e functional-feature -- --features-disable-after true   # force disable
tox -e functional-feature -- --features-disable-after false  # force keep enabled
```

Or directly with the virtualenv Python:

```bash
../.venv/bin/python -m pytest \
  tests/functional/feature/test_features.py::test_<feature_name> \
  --config tests/functional/feature/test_config.yaml \
  --features-disable-after true    # or false

Concrete examples:

- **Run TLS CA test and disable TLS after it completes**:

  ```bash
  tox -e functional-feature -- \
    tests/functional/feature/test_features.py::test_tls_ca \
    --features-disable-after true
  ```

- **Run Vault test and keep Vault enabled afterwards** (even if config sets
  `disable_after: true`):

  ```bash
  tox -e functional-feature -- \
    tests/functional/feature/test_features.py::test_vault \
    --features-disable-after false
  ```
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

- When `disable_after` is enabled (globally or per-feature), disable failures
  are **logged and ignored** so that the suite continues to the next feature.
