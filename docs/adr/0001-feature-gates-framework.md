# Use Feature Gates Framework for Sunbeam

<!--
status: accepted
date: 2026-02-23
decision-makers: Sunbeam Development Team
consulted: OpenStack operators, Canonical engineering teams
informed: Sunbeam users, downstream consumers
-->

## Context and Problem Statement

Sunbeam OpenStack needs a mechanism to control the visibility and availability of experimental or in-development features. Without such a mechanism, developers face several challenges:

1. **Development Risk**: New features must be immediately visible to all users once merged, creating pressure to delay merging until features are fully stable
2. **Progressive Rollout**: No way to gradually introduce features to subset of users for testing
3. **Experimental Features**: Difficulty in shipping experimental features without affecting production users
4. **Storage Backend Expansion**: New storage backends (e.g., Ceph RGW, NFS) need testing in production environments before general availability
5. **Multi-Release Support**: Features need different availability across different Sunbeam release channels (edge, beta, candidate, stable)

## Decision Drivers

* **Development velocity**: Need to merge features early without affecting production users
* **Progressive rollout**: Must support gradual feature adoption and testing
* **Multi-node consistency**: Configuration must be consistent across cluster nodes
* **Operator simplicity**: Configuration should be intuitive and easy to understand  
* **Framework integration**: Must work seamlessly with existing Click CLI framework
* **Unified approach**: Storage backends and features need same gating mechanism

## Considered Options

* **Feature Gates Framework** - Implement mixin-based framework with properties (`gate_key`, `is_gated`, `is_visible`), CLI decorators, two-tier configuration (snap config for gates, clusterd for enabled state), and automatic gate resolution
* **Environment Variables** - Use `SUNBEAM_ENABLE_<FEATURE>=true` environment variables to control feature availability
* **Configuration Files** - Manage feature gates via YAML/JSON files (e.g., `/etc/sunbeam/features.yaml`)
* **Runtime Detection** - Auto-detect feature availability based on installed dependencies, APIs, and cluster state
* **CLI Flags** - Require `--experimental` flag on every command that uses experimental features
* **Snap Channel Gates** - Tie feature availability solely to snap release channel (stable/candidate/beta/edge)

## Decision Outcome

**Chosen option: "Feature Gates Framework"**, because it provides:

* Fine-grained per-feature control (not just snap-channel level)
* Persistent configuration across restarts
* Multi-node cluster consistency through clusterd
* Integration with existing snap configuration system
* Pythonic property-based API for developers
* Backward compatibility with existing features

### Implementation Patterns

The framework supports two complementary implementation patterns based on feature type:

#### Pattern 1: Standalone Feature Classes (FeatureGateMixin)

For complete, independent features implemented as classes in `sunbeam/features/` or `sunbeam/storage/`:

```python
class MyFeature(OpenStackControlPlaneFeature):
    name = "my-feature"
    generally_available = False  # Feature is gated
    
    # Implementation...
```

**Used for:**
- New OpenStack services that can be enabled/disabled independently
- Storage backends with their own enable/disable commands
- Complete feature modules with dedicated CLI commands

**Benefits:**
- Feature appears/disappears from CLI automatically
- State persisted in clusterd for multi-node consistency
- Feature classes handle their own lifecycle

#### Pattern 2: Embedded Features (FEATURE_GATES Configuration)

For features embedded within existing commands (options, role types, entire commands):

```python
# sunbeam/feature_gates.py
FEATURE_GATES: dict[str, dict[str, bool]] = {
    "feature.multi-region": {
        "generally_available": False,  # Feature is gated
    },
}
```

Then use decorators and mappings:

```python
# Gate command options
@feature_gate_option("--region-controller-token", gate_key="feature.multi-region", ...)
def bootstrap(...): pass

# Gate entire commands
@feature_gate_command(gate_key="feature.multi-region")
def add_secondary_region_node(...): pass

# Gate enum values/choices
ROLE_GATES: dict[Role, str] = {
    Role.REGION_CONTROLLER: "feature.multi-region",
}
```

**Used for:**
- Options added to existing commands (bootstrap, join, etc.)
- New role types or deployment modes
- Entire commands that extend existing functionality
- Choice values in existing options

**Benefits:**
- Single configuration point in `FEATURE_GATES`
- Clean GA transition: flip `generally_available` to `True`, decorators become no-ops
- No code removal needed when graduating features
- Centralized feature gate registry for discoverability

**GA Transition Process:**
```python
# During development/beta
FEATURE_GATES = {
    "feature.multi-region": {
        "generally_available": False,  # Requires snap config to enable
    },
}

# After GA release
FEATURE_GATES = {
    "feature.multi-region": {
        "generally_available": True,  # Always available, decorators are no-ops
    },
}
```

This allows features to graduate to GA with a single-line configuration change, without removing decorator code scattered throughout the codebase.

### Multi-Node Synchronization

The framework includes a daemon-based synchronization solution for multi-node deployments:

**Architecture:**
- **Cluster Database (dqlite)**: Single source of truth for feature gate state, automatically replicated
- **Snap Configure Hook (Python)**: One-way sync from local snap config → cluster DB (reads `feature` namespace, including nested `feature.storage.*` keys)
- **Daemon Watcher (Go)**: Background goroutine that polls cluster DB every 5 seconds and syncs to all nodes' snap configs

**Data Flow:**
```
User: snap set openstack feature.X=true (or feature.storage.Y=true, any node)
  ↓
Configure Hook: Reads snap config → Pushes to cluster DB
  ↓
Cluster DB: dqlite replicates to all nodes
  ↓
Daemon Watcher: Polls DB (5s) → Calls snapctl set on all nodes
  ↓
Result: All nodes synchronized within 5-10 seconds
```

**Benefits:**
- No race conditions (clear separation: hook pushes to DB, daemon pulls from DB)
- Debounce protection prevents circular triggers
- Graceful error handling with automatic retries
- Eventually consistent across all cluster nodes
- Last-write-wins conflict resolution via dqlite
- Bootstrap node is authoritative source; join nodes do not sync during join phase to prevent conflicts

See [Multi-Node Feature Gate Synchronization ADR](../FEATURE_GATES.md#multi-node-feature-gate-synchronization) for detailed implementation.

### Consequences

* **Good**, because developers can merge incomplete features without affecting production
* **Good**, because features can be tested in specific clusters before general availability  
* **Good**, because operators have fine-grained control over feature adoption
* **Good**, because all features use consistent gating mechanism
* **Good**, because existing features work without modification (backward compatible)
* **Good**, because features graduate to GA with minimal code changes (flip one flag)
* **Good**, because embedded features don't require code removal at GA (decorators become no-ops)
* **Good**, because two patterns address different feature types appropriately
* **Good**, because daemon-based sync ensures multi-node consistency automatically
* **Good**, because comprehensive test coverage (89 Go tests + Python tests) validates implementation
* **Bad**, because operators need to understand snap config vs clusterd distinction
* **Bad**, because features should be tested in both gated and ungated states
* **Bad**, because initial snap config changes are node-local (mitigated by daemon sync in 5-10 seconds)
* **Bad**, because developers must choose appropriate pattern for their feature type

### Confirmation

Compliance with this decision can be confirmed through:

* **Python Unit Tests**: Test suite covers gated/ungated states for all features (sunbeam-python/tests/)
* **Go Unit Tests**: 89 test cases across 3 files validate sync daemon, API layer, and business logic:
  - `sunbeam-microcluster/sunbeam/feature_gate_sync_test.go` (7 tests)
  - `sunbeam-microcluster/sunbeam/feature_gates_test.go` (9 tests)
  - `sunbeam-microcluster/api/feature_gates_test.go` (7 tests)
  - Includes critical regression test for double-prefixing bug
* **Type Checking**: mypy passes with no type errors (ensures proper use of properties and mixin)
* **Code Review**: 
  - Standalone features must inherit `FeatureGateMixin` and set `generally_available` appropriately
  - Embedded features must be registered in `FEATURE_GATES` with proper `generally_available` flag
  - Decorators (`@feature_gate_option`, `@feature_gate_command`) must reference valid gate keys
* **Integration Testing**: Multi-node scenarios must verify cluster-wide consistency via clusterd
* **Documentation Review**: Each gated feature must document enablement instructions
* **GA Readiness**: Features being promoted to GA must update `generally_available` flag (both patterns)
* **Future Fitness Function**: Consider ArchUnit-style tests to automatically verify:
  - All feature classes inherit `FeatureGateMixin`
  - All gate decorators reference keys defined in `FEATURE_GATES`

## Pros and Cons of the Options (Rejected Alternatives)

### Option 1: Feature Flags via Environment Variables

Use environment variables to control feature availability:
```bash
export SUNBEAM_ENABLE_MULTI_REGION=true
sunbeam bootstrap ...
```

* **Good**, because simple and universally understood
* **Good**, because no additional configuration system needed
* **Good**, because easy to set per-session for testing
* **Bad**, because not persistent across restarts or sessions
* **Bad**, because difficult to manage in multi-node clusters
* **Bad**, because no integration with snap/clusterd infrastructure
* **Bad**, because service restarts lose configuration

### Option 2: Feature Flags in Configuration Files

Use YAML/JSON configuration files to manage feature gates:
```yaml
# /etc/sunbeam/features.yaml
features:
  multi-region:
    enabled: true
  experimental-storage:
    enabled: false
```

* **Good**, because centralized and version-controllable
* **Good**, because rich structure for complex settings
* **Good**, because familiar pattern for operators
* **Bad**, because requires file synchronization across cluster nodes
* **Bad**, because no integration with existing snap configuration
* **Bad**, because additional file management complexity
* **Bad**, because potential for configuration drift

### Option 3: Runtime Feature Detection Only

No explicit gates; features auto-detect based on environment:
- Detect if required dependencies installed
- Check if APIs available  
- Infer capability from cluster state

* **Good**, because no operator configuration needed
* **Good**, because automatic, self-adapting behavior
* **Bad**, because no explicit control over experimental features
* **Bad**, because can't prevent access to incomplete features
* **Bad**, because debugging is harder (unpredictable availability)
* **Bad**, because can't do progressive rollouts or A/B testing

### Option 4: Per-Command CLI Flags

Require explicit flag for experimental features on every command:
```bash
sunbeam enable multi-region --experimental
sunbeam configure multi-region --experimental --region-name X
```

* **Good**, because very explicit when using experimental features
* **Good**, because no persistent state needed
* **Good**, because easy to implement
* **Bad**, because poor user experience (repetitive flags)
* **Bad**, because can't hide features from help output
* **Bad**, because no cluster-wide consistency
* **Bad**, because difficult to graduate features to stable

### Option 5: Risk-Level Based Gates Only

Gate features based solely on release channel (stable/candidate/beta/edge):
- Edge snap: all features available
- Beta snap: only stable + beta features
- Stable snap: only GA features

* **Good**, because simple model tied to release channels
* **Good**, because automatic behavior based on snap installation
* **Good**, because no per-feature configuration needed
* **Neutral**, because could be complementary to chosen solution
* **Bad**, because too coarse-grained (can't enable single experimental feature)
* **Bad**, because can't test experimental features in production before GA
* **Bad**, because no flexibility for operators
* **Bad**, because doesn't address per-cluster or per-feature needs

## More Information

### Implementation Files

**Python (sunbeam-python/):**
* **`sunbeam/feature_gates.py`**: Core implementation with `FEATURE_GATES` configuration, decorators, and utilities
* **`sunbeam/feature_manager.py`**: Feature registration with gate checking
* **`sunbeam/hooks.py`**: Snap configure hook with feature gate sync to cluster
* **`sunbeam/clusterd/client.py`**: Client API for feature gate CRUD operations
* **`sunbeam/clusterd/models.py`**: FeatureGate and FeatureGates pydantic models
* **`sunbeam/core/common.py`**: `ROLE_GATES` mapping for role-based feature gating
* **`sunbeam/provider/local/commands.py`**: Multi-region feature implementation (local provider)
* **`sunbeam/provider/maas/commands.py`**: Multi-region feature implementation (MAAS provider)
* **`sunbeam/storage/base.py`**: `FeatureGateMixin` for storage backend gating
* **`sunbeam/features/interface/v1/base.py`**: `FeatureGateMixin` for feature classes

**Go (sunbeam-microcluster/):**
* **`sunbeam/feature_gate_sync.go`**: Daemon watcher for cluster DB → snap config sync
* **`sunbeam/feature_gates.go`**: Business logic for feature gate CRUD operations
* **`api/feature_gates.go`**: REST API endpoints (/1.0/feature-gates)
* **`api/apitypes/feature_gates.go`**: API type definitions
* **`database/feature_gate.go`**: Database schema and ORM mappings
* **`database/schema.go`**: FeatureGatesSchemaUpdate for dqlite table
* **`cmd/sunbeamd/main.go`**: Daemon startup with sync watcher initialization

### References

* [Developer Documentation](../FEATURE_GATES.md) - Complete usage guide with examples for both patterns
* [Multi-Node Sync Documentation](../FEATURE_GATES.md#multi-node-feature-gate-synchronization) - Detailed daemon architecture
* [PR #676](https://github.com/canonical/snap-openstack/pull/676) - Implementation pull request with review feedback
* [Feature Flags Best Practices](https://martinfowler.com/articles/feature-toggles.html) - Martin Fowler's guide
* [MADR](https://adr.github.io/madr/) - ADR template format used

### Related Decisions

None yet - This is the first ADR in the Sunbeam project.
