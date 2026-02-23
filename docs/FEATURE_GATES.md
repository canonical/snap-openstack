# Sunbeam Feature Gates Framework

## Overview

The Sunbeam feature gates framework provides a unified way to control the visibility and availability of features, commands, and options in Sunbeam. This allows developers to:

1. Hide experimental or beta features from users until they explicitly enable them
2. Gate specific command options (like multi-region support)
3. Control storage backend availability
4. Manage feature rollout and testing

## How Feature Gates Work

Feature gates are controlled via snap configuration:

```bash
# Enable a feature
sudo snap set openstack feature.<feature-name>=true

# Enable a storage backend
sudo snap set openstack storage.<backend-type>=true

# Disable a feature
sudo snap set openstack feature.<feature-name>=false

# Check current configuration
sudo snap get openstack feature
```

## Types of Feature Gates

Sunbeam supports two types of feature gates:

### 1. Standalone Feature Gates (Feature Classes)

For features that are implemented as complete, independent modules in `sunbeam/features/`, use the `generally_available` class attribute:

```python
class MyFeature(OpenStackControlPlaneFeature):
    name = "my-feature"
    generally_available = False  # Feature is gated
```

This pattern is used for:
- New OpenStack services/features that can be enabled/disabled
- Storage backends
- Complete feature modules with their own enable/disable commands

### 2. Embedded Feature Gates (FEATURE_GATES Configuration)

For features that are embedded within existing commands (not standalone features), use the `FEATURE_GATES` configuration dictionary in `sunbeam/feature_gates.py`:

```python
# sunbeam/feature_gates.py
FEATURE_GATES: dict[str, dict[str, bool]] = {
    "feature.multi-region": {
        "generally_available": False,  # TODO: Set to True when multi-region is GA
    },
}
```

This pattern is used for:
- Options added to existing commands (via `@feature_gate_option`)
- Entire commands gated via `@feature_gate_command`
- Role/choice values gated via `FeatureGatedChoice`

**Key benefit**: When making a feature GA, simply flip `generally_available` to `True` in `FEATURE_GATES`, and all decorators and filtered choices automatically adapt - no code removal needed!

**Example GA transition**:
```python
# Before GA (feature is gated)
FEATURE_GATES = {
    "feature.multi-region": {
        "generally_available": False,
    },
}

# After GA (feature is always available)
FEATURE_GATES = {
    "feature.multi-region": {
        "generally_available": True,  # Just flip this flag!
    },
}
```

Once `generally_available=True`, the decorators become no-ops and gated choices become always available - no need to remove any decorator code!

## Use Cases

### 1. Gating Features in `sunbeam/features`

Features that can be enabled/disabled (classes extending `EnableDisableFeature` or `OpenStackControlPlaneFeature`) can be gated by setting `generally_available = False`.

#### Example: Creating a Gated Feature

```python
# sunbeam/features/my_experimental_feature/feature.py
from packaging.version import Version
import click

from sunbeam.core.deployment import Deployment
from sunbeam.core.manifest import FeatureConfig, SoftwareConfig
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj


class MyExperimentalFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")
    name = "my-experimental-feature"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO
    
    # Gate this feature - will be hidden unless user enables it
    generally_available = False

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig()

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {}

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        return ["my-experimental-app"]

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-my-experimental-feature": True,
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-my-experimental-feature": False,
        }

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable My Experimental Feature."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable My Experimental Feature."""
        self.disable_feature(deployment, show_hints)
```

**To use the feature:**

```bash
# Feature is hidden by default
sunbeam enable --help  # my-experimental-feature not shown

# Enable the feature gate
sudo snap set openstack feature.my-experimental-feature=true

# Now the feature is available
sunbeam enable --help  # my-experimental-feature is shown
sunbeam enable my-experimental-feature
```

### 2. Gating Command Options (e.g., Multi-Region Support)

Use the `@feature_gate_option` decorator to add options that are only visible when the gate is enabled.

#### Step 1: Register the Feature Gate

First, add the feature to `FEATURE_GATES` in `sunbeam/feature_gates.py`:

```python
# sunbeam/feature_gates.py
FEATURE_GATES: dict[str, dict[str, bool]] = {
    "feature.multi-region": {
        "generally_available": False,  # Feature is gated
    },
}
```

#### Step 2: Apply Decorators to Commands

Then use the `@feature_gate_option` decorator in your commands:

```python
# sunbeam/provider/local/commands.py
import click
from sunbeam.feature_gates import feature_gate_option


@click.command()
@click.option(
    "--role",
    "roles",
    multiple=True,
    default=["control", "compute"],
    help="Specify roles for the node"
)
@feature_gate_option(
    "--region-controller-token",
    gate_key="feature.multi-region",
    type=str,
    help="Token for connecting to the region controller"
)
@feature_gate_option(
    "--region-controller-endpoint",
    gate_key="feature.multi-region",
    type=str,
    help="Endpoint URL for the region controller"
)
def bootstrap(roles, region_controller_token=None, region_controller_endpoint=None):
    """Bootstrap the Sunbeam cluster."""
    if region_controller_token:
        if not region_controller_endpoint:
            click.echo("Connecting to region controller...")
            # Multi-region setup logic here
        else:
            click.echo(f"Connecting to region controller at {region_controller_endpoint}")
    else:
        click.echo("Setting up standard deployment")
    # Normal bootstrap logic here
```

#### Step 3: Making the Feature GA

When ready for general availability, just update `FEATURE_GATES`:

```python
# sunbeam/feature_gates.py
FEATURE_GATES: dict[str, dict[str, bool]] = {
    "feature.multi-region": {
        "generally_available": True,  # Now GA! Options always visible
    },
}
```

No need to remove any decorator code - they automatically become no-ops!

**To use multi-region options:**

```bash
# Multi-region options are hidden by default
sunbeam cluster bootstrap --help  # Regional options not shown

# Enable the multi-region gate
sudo snap set openstack feature.multi-region=true

# Now the multi-region options are available
sunbeam cluster bootstrap --help  # Regional options shown
sunbeam cluster bootstrap --role control,compute,storage --region-controller-token=$token

# Or add a secondary region node
sunbeam cluster add-secondary-region-node $fqdn
```

### 3. Gating Specific Choice Values

Use the `FeatureGatedChoice` type to gate specific values within a Click Choice option. This is useful for making certain role types or deployment modes only available when feature gates are enabled.

#### Example: Gating region_controller Role

```python
# sunbeam/provider/local/commands.py
import click
from sunbeam.core.common import Role
from sunbeam.feature_gates import FeatureGatedChoice


@click.command()
@click.option(
    "--role",
    "roles",
    multiple=True,
    type=FeatureGatedChoice(
        choices=["control", "compute", "storage", "region_controller"],
        gated_choices={"feature.multi-region": ["region_controller"]}
    ),
    default=["control", "compute"],
    help="Specify roles for the node"
)
def bootstrap(roles):
    """Bootstrap the Sunbeam cluster."""
    if "region_controller" in roles:
        click.echo("Setting up region controller deployment")
        # Multi-region setup logic here
    else:
        click.echo("Setting up standard deployment")
    # Normal bootstrap logic here
```

**How it works:**

- When `feature.multi-region` is **disabled**:
  - `--role` accepts: `control`, `compute`, `storage` 
  - `--role region_controller` fails with: "Invalid value. Available choices: control, compute, storage"
  - Help text shows: `[control|compute|storage|region_controller]  Note: region_controller (requires feature.multi-region)`

- When `feature.multi-region` is **enabled**:
  - `--role` accepts: `control`, `compute`, `storage`, `region_controller`
  - All choices work normally
  - Help text shows: `[control|compute|storage|region_controller]`

**Usage:**

```bash
# Without feature gate - region_controller not available
sunbeam bootstrap --role region_controller
# Error: Invalid value 'region_controller'. Available choices: control, compute, storage

# Enable the multi-region gate
sudo snap set openstack feature.multi-region=true

# Now region_controller role is available
sunbeam bootstrap --role region_controller
# Success: Setting up region controller deployment
```

#### Alternative: Using Role Enums with ROLE_GATES

For better type safety and centralized role management, you can use the `Role` enum with `ROLE_GATES` mapping:

```python
# sunbeam/core/common.py
from sunbeam.feature_gates import is_feature_gate_enabled

class Role(str, enum.Enum):
    CONTROL = "control"
    COMPUTE = "compute"
    STORAGE = "storage"
    REGION_CONTROLLER = "region_controller"
    
    @classmethod
    def enabled_values(cls) -> list[str]:
        """Return list of enabled role values based on feature gates."""
        return [role.name.lower() for role in cls if _is_role_enabled(role)]

# Map roles to their feature gates
ROLE_GATES: dict[Role, str] = {
    Role.REGION_CONTROLLER: "feature.multi-region",
}

def _is_role_enabled(role: Role) -> bool:
    """Check if a role is enabled based on its feature gate."""
    gate_key = ROLE_GATES.get(role)
    if not gate_key:
        return True  # Not gated
    return is_feature_gate_enabled(gate_key)
```

Then use it in commands:

```python
# sunbeam/provider/local/commands.py
import click
from sunbeam.core.common import Role

@click.command()
@click.option(
    "--role",
    type=click.Choice(Role.enabled_values()),
    default="control",
    help="Role for this node"
)
def bootstrap(role):
    """Bootstrap the Sunbeam cluster."""
    if role == Role.REGION_CONTROLLER.value:
        click.echo("Setting up region controller deployment")
    else:
        click.echo("Setting up standard deployment")
```

This pattern:
- Centralizes role definitions in one place
- Maps roles to gates in `ROLE_GATES` configuration
- Automatically filters choices based on GA status
- Provides type safety with enums

#### Example: Multiple Gated Choices

```python
@click.option(
    "--deployment-type",
    type=FeatureGatedChoice(
        choices=["single", "ha", "multi-region", "experimental"],
        gated_choices={
            "feature.multi-region": ["multi-region"],
            "feature.experimental-deployments": ["experimental"]
        }
    ),
    default="single",
    help="Type of deployment"
)
def deploy(deployment_type):
    click.echo(f"Deploying {deployment_type} configuration")
```

### 4. Gating Entire Commands

Use the `@feature_gate_command` decorator to gate entire commands.

#### Step 1: Register the Feature Gate

Add the feature to `FEATURE_GATES` in `sunbeam/feature_gates.py`:

```python
# sunbeam/feature_gates.py
FEATURE_GATES: dict[str, dict[str, bool]] = {
    "feature.experimental-commands": {
        "generally_available": False,
    },
}
```

#### Step 2: Apply Decorator to Command

```python
# sunbeam/commands/experimental.py
import click
from sunbeam.feature_gates import feature_gate_command


@click.command()
@feature_gate_command(
    gate_key="feature.experimental-commands",
    hidden_message=(
        "Experimental commands are not enabled. "
        "Enable with: sudo snap set openstack feature.experimental-commands=true"
    )
)
def experimental_diagnostics():
    """Run experimental diagnostic checks."""
    click.echo("Running experimental diagnostics...")
    # Experimental logic here
```

**To use gated commands:**

```bash
# Command shows error message when gate is disabled
sunbeam experimental-diagnostics
# Error: Experimental commands are not enabled. Enable with: sudo snap set openstack feature.experimental-commands=true

# Enable the gate
sudo snap set openstack feature.experimental-commands=true

# Now the command works
sunbeam experimental-diagnostics
# Running experimental diagnostics...
```

### 5. Storage Backend Gates (Already Implemented)

Storage backends automatically inherit `FeatureGateMixin` and use the same pattern.

#### Example: Creating a Gated Storage Backend

```python
# sunbeam/storage/my_storage/backend.py
from packaging.version import Version
from sunbeam.storage.base import StorageBackendBase, StorageBackendConfig


class MyStorageConfig(StorageBackendConfig):
    """Configuration for My Storage backend."""
    server_url: str
    credentials: dict


class MyStorageBackend(StorageBackendBase[MyStorageConfig]):
    backend_type = "my-storage"
    display_name = "My Storage System"
    version = Version("0.0.1")
    
    # Gate this backend - will be hidden unless user enables it
    generally_available = False

    # Implementation of required methods...
```

**To use the storage backend:**

```bash
# Backend is hidden by default
sunbeam storage add --help  # my-storage not shown

# Enable the storage backend gate
sudo snap set openstack storage.my-storage=true

# Now the backend is available
sunbeam storage add --help  # my-storage is shown
sunbeam storage add my-storage --server-url=... --credentials=...
```

## Programmatic Gate Checking

For cases where you need to check gates in code without decorators:

```python
from sunbeam.feature_gates import is_feature_gate_enabled, check_feature_gate, FeatureGateError


def setup_advanced_networking():
    """Setup advanced networking if gate is enabled."""
    if is_feature_gate_enabled("feature.advanced-networking"):
        # Advanced networking logic
        setup_multi_tenant_networking()
    else:
        # Standard networking logic
        setup_basic_networking()


def require_experimental_mode():
    """Function that requires experimental mode."""
    try:
        check_feature_gate(
            "feature.experimental",
            error_message="Experimental mode required. Enable with: sudo snap set openstack feature.experimental=true"
        )
    except FeatureGateError as e:
        click.echo(str(e))
        return
    
    # Experimental logic here
```

## Implementation Details

### FeatureGateMixin

The `FeatureGateMixin` class provides the core gating functionality:

- **`gate_key`**: Read-only property that returns the snap config key for the feature gate
- **`is_gated`**: Read-only property that checks if the feature is currently gated (hidden)
- **`is_visible`**: Read-only property that checks if the feature is visible (not gated) - inverse of `is_gated`
- **`check_gated(client, snap, enabled_config_key)`**: Method for advanced gate checks with custom parameters

### Gate Resolution Order

Feature gates are resolved in the following order:

1. **Generally Available Flag**: If `generally_available = True`, the feature is always visible
2. **Cluster Config**: For storage backends, checks if enabled in cluster database
3. **Snap Config**: Checks the snap configuration for the gate key

### Multi-Node Considerations

**Automatic Synchronization (Local Deployments)**: Feature gates are automatically synchronized across all nodes in a local multi-node deployment through a daemon-based architecture. When you enable a feature gate on any node:

```bash
# Run on ANY node in the cluster
sudo snap set openstack feature.multi-region=true
```

**What happens automatically:**
1. Snap configure hook pushes the change to cluster database (dqlite)
2. Database replicates to all cluster nodes
3. Daemon watcher on each node (polls every 5 seconds) detects the change
4. Daemon updates snap config on all nodes within 5-10 seconds
5. All nodes have consistent configuration - no manual sync needed!

**Architecture:**
- **Cluster Database**: Single source of truth, automatically replicated by dqlite
- **Configure Hook**: One-way sync from snap config → cluster DB (all deployment types)
- **Daemon Watcher**: Background process that checks deployment type on every iteration
  - For **local** deployments: Syncs cluster DB → snap config (bidirectional)
  - For **MAAS** deployments: Skips sync (one-way only: snap → cluster)
- **No Race Conditions**: Clear separation of responsibilities prevents conflicts

**MAAS Deployments**: In MAAS deployments, the daemon watcher **skips sync on every iteration**. Each node manages its own snap configuration independently:
- Feature gates are still stored in the cluster database for visibility
- Configure hook syncs snap config → cluster DB (one-way only)
- Daemon watcher checks deployment type and skips writeback from cluster DB → snap config
- Set feature gates individually on each MAAS node as needed
- Handles race conditions where daemon starts before bootstrap completes

**For Active Features:**
- Once a feature is enabled and in use, its state is tracked in clusterd
- Feature gates persist across cluster operations and node restarts
- To disable a gated feature, disable the feature itself first, then the gate

See [FEATURE_GATES_MULTINODE.md](FEATURE_GATES_MULTINODE.md) for detailed architecture, data flows, and troubleshooting.

### Integration Points

1. **FeatureManager**: Checks feature gates during CLI registration
2. **StorageBackendManager**: Checks backend gates when listing available backends
3. **Click Commands**: Decorators integrate with Click's command system

## Best Practices

1. **Use Descriptive Gate Names**: Use clear, hierarchical names like `feature.multi-region`
2. **Document Requirements**: Clearly document which snap config needs to be set
3. **Provide Helpful Messages**: Use `hidden_message` parameter to guide users
4. **Default to Gated for Experimental**: Set `generally_available = False` for new/experimental features
5. **Graduate Features**: Set `generally_available = True` when features are stable and ready for general use
6. **Test Both States**: Test features with gates both enabled and disabled

## Advanced Patterns

### Creating Wrapper Decorators

For features with multiple related options, create wrapper decorators to reduce boilerplate:

```python
# sunbeam/features/multiregion/decorators.py
from functools import wraps
from sunbeam.feature_gates import feature_gate_option


def multi_region_option(*param_decls, **attrs):
    """Convenience decorator for multi-region options.
    
    Wraps feature_gate_option with the multi-region gate key pre-configured.
    """
    return feature_gate_option(
        *param_decls,
        gate_key="feature.multi-region",
        **attrs
    )


# Usage in commands:
@click.command()
@click.option("--role", multiple=True, default=["control", "compute"])
@multi_region_option(
    "--region-controller-token",
    type=str,
    help="Token for connecting to the region controller"
)
@multi_region_option(
    "--region-controller-endpoint",
    type=str,
    help="Endpoint URL for the region controller"
)
def bootstrap(role, region_controller_token=None, region_controller_endpoint=None):
    """Bootstrap the Sunbeam cluster."""
    # Implementation here
    pass
```

This pattern:
- Reduces repetition when multiple options share the same gate
- Makes code more maintainable by centralizing the gate key
- Provides a clear, domain-specific API for feature-specific options

## Testing

### Unit Tests

```python
from unittest.mock import MagicMock, patch
from snaphelpers import Snap, UnknownConfigKey
from sunbeam.feature_gates import is_feature_gate_enabled


def test_feature_gate_enabled():
    """Test feature gate when enabled."""
    mock_snap = MagicMock(spec=Snap)
    mock_snap.config.get.return_value = True
    
    with patch('sunbeam.feature_gates.Snap', return_value=mock_snap):
        assert is_feature_gate_enabled("feature.test")


def test_feature_gate_disabled():
    """Test feature gate when disabled."""
    mock_snap = MagicMock(spec=Snap)
    mock_snap.config.get.side_effect = UnknownConfigKey("")
    
    with patch('sunbeam.feature_gates.Snap', return_value=mock_snap):
        assert not is_feature_gate_enabled("feature.test")
```

### Functional Tests

```bash
#!/bin/bash
# Test multi-region feature gate

# Verify option is hidden by default
sunbeam cluster bootstrap --help | grep -q "region-controller" && echo "FAIL: Option visible when gated" || echo "PASS: Option hidden"

# Enable the gate
sudo snap set openstack feature.multi-region=true

# Verify option is now visible
sunbeam cluster bootstrap --help | grep -q "region-controller" && echo "PASS: Option visible when enabled" || echo "FAIL: Option still hidden"

# Test option works
sunbeam cluster bootstrap --role control,compute,storage --region-controller-token=$token

# Cleanup
sudo snap set openstack feature.multi-region=false
```

## Migration Guide

### For Existing Standalone Features

To add gate support to an existing feature class:

1. Set `generally_available = False` in your feature class
2. Update documentation to mention the snap config requirement
3. Test with gate both enabled and disabled

### For New Standalone Features

New feature classes should be gated by default:

```python
class NewFeature(OpenStackControlPlaneFeature):
    name = "new-feature"
    generally_available = False  # Gated by default
```

### For Embedded Features in Commands

For options/commands embedded in existing commands:

1. Add entry to `FEATURE_GATES` in `sunbeam/feature_gates.py`:
   ```python
   FEATURE_GATES = {
       "feature.my-new-option": {
           "generally_available": False,
       },
   }
   ```

2. Use appropriate decorator:
   ```python
   @feature_gate_option("--my-option", gate_key="feature.my-new-option", ...)
   ```

3. When making GA, just flip the flag:
   ```python
   FEATURE_GATES = {
       "feature.my-new-option": {
           "generally_available": True,  # Feature is now GA!
       },
   }
   ```

## Troubleshooting

### Feature Not Showing After Enabling Gate

1. Check snap config is set correctly:
   ```bash
   sudo snap get openstack feature
   ```

2. Restart sunbeam CLI or reload:
   ```bash
   # Re-run the command to reload feature registration
   sunbeam --help
   ```

3. Check logs for gate-related messages:
   ```bash
   tail -f $HOME/snap/openstack/common/logs/<latest log file>
   ```

### Feature Shows When It Shouldn't

1. Check if `generally_available = True` is set (should be `False` to gate)
2. Verify no cluster config has enabled the feature
3. Check snap config is not accidentally set

## Reference

### Snap Configuration Keys

- `feature.<feature-name>`: Enable/disable a specific feature
- `storage.<backend-type>`: Enable/disable a storage backend

### Configuration Files

- `sunbeam/feature_gates.py`: Contains `FEATURE_GATES` configuration dictionary for embedded features
- Feature classes: Use `generally_available` attribute for standalone features

### Available Decorators and Classes

- `@feature_gate_option(...)`: Gate a click option based on snap configuration
- `@feature_gate_command(...)`: Gate an entire click command
- `FeatureGatedChoice(choices, gated_choices, case_sensitive)`: Click Choice type that gates specific choice values

### Utility Functions

- `is_feature_gate_enabled(gate_key)`: Check if a snap config gate is enabled
- `check_feature_gate(gate_key, error_message)`: Require a gate to be enabled (raises exception if not)
- `log_gated_feature(feature_name, gate_key)`: Log that a feature is gated

## Examples in Codebase

See these files for working examples:

- `sunbeam/feature_gates.py`: `FEATURE_GATES` configuration and core implementation
- `sunbeam/provider/local/commands.py`: Multi-region feature gates with `@feature_gate_option` and `@feature_gate_command`
- `sunbeam/provider/maas/commands.py`: Multi-region feature gates for MAAS provider
- `sunbeam/core/common.py`: `ROLE_GATES` mapping roles to feature gates
- `sunbeam/provider/maas/deployment.py`: `ROLETAG_GATES` for MAAS role tags
- `sunbeam/storage/base.py`: Storage backend gates
- `sunbeam/features/vault/feature.py`: Feature with conditional options

## Contributing

When adding new gated features:

1. Follow the patterns documented here
2. Add tests for both gated and ungated states
3. Document the snap config requirement in user-facing documentation
4. Update this guide with any new patterns or use cases
