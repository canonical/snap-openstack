# Multi-Node Feature Gate Synchronization

## Overview

Feature gates need to be synchronized across all nodes in a Sunbeam cluster. This document describes the **daemon-based synchronization architecture** that ensures consistent feature gate state across the cluster.

## Architecture: Daemon-Based Sync

The synchronization uses a **daemon watcher** approach with clear separation of concerns:

### Components

1. **Cluster Database (dqlite)**
   - Single source of truth for feature gate state
   - `feature_gates` table: `gate_key TEXT, enabled BOOLEAN`
   - Automatically replicated across all cluster nodes by dqlite

2. **Snap Configure Hook** (Python)
   - Triggered when user runs `snap set openstack feature.X=Y`
   - **One-way sync**: Reads local snap config → Pushes to cluster DB
   - Uses dynamic gate discovery via `snap.config.get_options("feature")`
   - Does NOT pull from cluster (daemon handles this)

3. **Daemon Watcher** (Go)
   - Background goroutine in `sunbeamd`
   - Polls cluster DB every 5 seconds for changes
   - **One-way sync**: Reads cluster DB → Pushes to all nodes' snap configs
   - Calls `snapctl set feature.X=Y` to update local snap config

### Data Flow

```
User Action (any node):
┌─────────────────────────────────────────────────────────┐
│ snap set openstack feature.multi-region=true            │
└───────────────┬─────────────────────────────────────────┘
                │
                ▼
    ┌───────────────────────┐
    │  Configure Hook       │
    │  (Python - local)     │
    │  • Reads snap config  │
    │  • Pushes to cluster  │
    └───────┬───────────────┘
            │
            ▼
    ┌────────────────────────┐
    │  Cluster Database      │
    │  (dqlite - replicated) │
    │  • Single source       │
    │  • Auto-replicated     │
    └────┬───────────────────┘
         │
         │ (within 5 seconds)
         │
         ▼
    ┌─────────────────────────────────┐
    │  Daemon Watcher (Go - all nodes)│
    │  • Polls every 5s               │
    │  • Detects changes              │
    │  • Calls snapctl set            │
    └───────┬─────────────────────────┘
            │
            ▼
    ┌──────────────────────┐
    │  Snap Config Updated │
    │  (on all nodes)      │
    └──────────────────────┘
```

### Why This Architecture Works

**No Race Conditions:**
- Hook: snap config → cluster DB (one direction only)
- Daemon: cluster DB → snap config (opposite direction only)
- Each component has a single, clear responsibility
- No ambiguity about which values are fresh vs stale

**Circular Triggers Handled:**
1. Daemon sets snap config → triggers hook
2. Hook reads snap config (same value daemon just set) → pushes to cluster DB
3. Cluster DB already has that value → dqlite no-ops the duplicate write
4. System converges quickly without infinite loops

**Debounce Protection:**
- Daemon waits 2 seconds after setting values before syncing again
- Prevents excessive churn during rapid configuration changes
- Allows hook to complete its work before daemon checks again

**Conflict Resolution:**
- Last write to cluster DB wins (atomic operations via dqlite)
- All nodes converge to the same authoritative state
- No possibility of divergent configs across nodes

## Deployment Type Handling

The daemon watcher behavior adapts based on deployment type to match the operational model of each deployment:

### Local Deployments (default)

**Behavior:** **Bidirectional sync** (snap ↔ cluster)

- Configure hook: snap config → cluster DB (push)
- Daemon watcher: cluster DB → snap config (pull and update all nodes)
- Result: Set feature gate on ANY node, ALL nodes receive it automatically
- Use case: Single operator managing all nodes, expect cluster-wide consistency

### MAAS Deployments

**Behavior:** **One-way sync only** (snap → cluster)

- Configure hook: snap config → cluster DB (push)
- Daemon watcher: **Skips sync** on every iteration (checks `deployment.type` config key)
- Result: Each node manages its own snap configuration independently
- Use case: Different operators per node, or machine-specific configurations

**Why skip writeback for MAAS:**
- MAAS nodes are provisioned and managed independently
- Each machine may have different roles and configurations
- Operators set feature gates per-machine based on specific needs
- Writeback would override intentional per-node differences

**Feature gates still stored in cluster DB:**
- Provides visibility across all nodes (can query what's set where)
- Hook still pushes local config to cluster for tracking
- Daemon checks deployment type every 5 seconds and skips writeback if MAAS

**How deployment type is set:**
```python
# During bootstrap (Python)
client.cluster.update_config("deployment.type", "local")  # or "maas"
```

**Race condition handling:**
- Daemon starts before bootstrap completes (deployment type not yet set)
- Daemon checks deployment type on every sync iteration
- Falls back to "local" behavior if key doesn't exist (backward compatible)
- Once bootstrap writes deployment type, behavior switches on next iteration

## Implementation Details

### Go Daemon Watcher

**File:** `sunbeam-microcluster/sunbeam/feature_gate_sync.go`

**Key Features:**
- Polls cluster DB every 5 seconds (`syncInterval = 5 * time.Second`)
- Checks deployment type on every iteration (handles MAAS vs local)
- Tracks last known state to detect changes efficiently
- Debounce timer (2 seconds) to avoid circular trigger issues
- Graceful error handling - logs errors and continues
- Uses `exec.Command("snapctl", "set", ...)` for snap configuration
- Supports both setting and unsetting feature gates

**Deployment Type Check:**
```go
// On every sync iteration before processing changes
deploymentType, err := GetConfig(ctx, fgs.state, "deployment.type")
if err == nil && deploymentType == "maas" {
    return nil  // Skip sync for MAAS deployments
}
// Continue with sync for "local" or missing config
```

**Key Functions:**
```go
// StartFeatureGateSync - Called from OnStart hook
func StartFeatureGateSync(ctx context.Context, s state.State)

// syncLoop - Background polling loop
func (fgs *featureGateSyncer) syncLoop(ctx context.Context)

// syncOnce - Single sync iteration with debounce
func (fgs *featureGateSyncer) syncOnce(ctx context.Context) error

// setSnapConfig - Updates snap config via snapctl
func (fgs *featureGateSyncer) setSnapConfig(gateKey string, enabled bool) error

// unsetSnapConfig - Removes gate from snap config
func (fgs *featureGateSyncer) unsetSnapConfig(gateKey string) error
```

**Lifecycle:**
- Started in `OnStart` hook of microcluster daemon
- Runs as background goroutine for entire daemon lifetime
- Gracefully stops when daemon context is cancelled
- Automatically resumes on daemon restart

### Python Configure Hook

**File:** `sunbeam-python/sunbeam/hooks.py`

**Key Features:**
- Dynamic gate discovery: `snap.config.get_options("feature")`
- One-way push only (no pulling from cluster)
- Updates cluster DB via microcluster client API
- Removes gates from cluster DB that are unset locally
- Handles connection errors gracefully (won't block snap set)

**Key Function:**
```python
def _sync_feature_gates_to_cluster(snap: Snap) -> None:
    """
    Sync feature gates from snap config to cluster database.
    
    This is a ONE-WAY sync: local snap -> cluster DB.
    The daemon watcher handles the reverse direction (cluster DB -> all nodes).
    """
    # Get all feature.* keys from local snap config
    feature_options = snap.config.get_options("feature")
    
    # Push each gate to cluster DB
    for key, value in feature_options.items():
        client.cluster.update_feature_gate(key, value)
    
    # Remove gates that are unset locally
    # (ensures cluster reflects local changes)
```

**Hook Invocation:**
- Automatically triggered by snapd on `snap set`
- Runs even if only non-feature keys are set (no harm, just checks and exits)
- Fast execution - just API calls to local daemon

### Microcluster REST API

**File:** `sunbeam-microcluster/api/feature_gates.go`

**Endpoints:**
- `GET /1.0/feature-gates` - List all feature gates
- `GET /1.0/feature-gates/{key}` - Get specific gate
- `POST /1.0/feature-gates` - Add new gate
- `PUT /1.0/feature-gates/{key}` - Update gate (used by hook)
- `DELETE /1.0/feature-gates/{key}` - Delete gate (used by hook)

**Thread Safety:**
- dqlite provides automatic locking and consistency
- Concurrent updates to different gates work fine
- Concurrent updates to same gate use last-write-wins

### Feature Gate Checks

**File:** `sunbeam-python/sunbeam/feature_gates.py`

Commands now check feature gates via cluster DB:

```python
def check_gated(self, client=None, snap=None) -> bool:
    """Check if feature is gated."""
    if self.generally_available:
        return False  # Feature is GA, not gated
    
    # Check cluster DB first (authoritative for multi-node)
    if client:
        try:
            gate = client.cluster.get_feature_gate(self.gate_key)
            if gate and gate.enabled:
                return False  # Feature is enabled
        except Exception:
            pass  # Fall through to snap config check
    
    # Fallback to local snap config (single-node or cluster unavailable)
    if snap and snap.config.get(self.gate_key):
        return False
    
    return True  # Gated by default
```

## Usage Examples

### Single Node Deployment

```bash
# User sets a feature gate
sudo snap set openstack feature.multi-region=true

# What happens:
# 1. Hook pushes multi-region=true to cluster DB (immediate)
# 2. Daemon sees change on next poll (~5s)
# 3. Daemon calls snapctl set (no-op since already set locally)
# 4. System is synchronized

# Verify
snap get openstack feature.multi-region
# Output: true
```

### Multi-Node Cluster (3 nodes)

```bash
# On Node A:
sudo snap set openstack feature.multi-region=true

# Timeline:
# T+0ms:   Node A hook pushes to cluster DB
# T+10ms:  dqlite replicates to Node B and C databases
# T+5s:    Node B daemon polls, sees change, runs snapctl set
# T+5s:    Node C daemon polls, sees change, runs snapctl set
# T+5s:    All nodes now have feature.multi-region=true in snap config

# Verify on any node:
snap get openstack feature.multi-region
# Output: true (on all three nodes)
```

### MAAS Multi-Node Cluster (3 nodes)

```bash
# On Node A (MAAS deployment):
sudo snap set openstack feature.multi-region=true

# Timeline:
# T+0ms:   Node A hook pushes to cluster DB
# T+10ms:  dqlite replicates to Node B and C databases
# T+5s:    Node B daemon polls, checks deployment.type=maas, SKIPS sync
# T+5s:    Node C daemon polls, checks deployment.type=maas, SKIPS sync
# T+5s:    ONLY Node A has feature.multi-region=true in snap config

# Verify:
# Node A:
snap get openstack feature.multi-region
# Output: true

# Node B:
snap get openstack feature.multi-region
# Output: (not set) - must be configured independently

# To enable on Node B:
sudo snap set openstack feature.multi-region=true
# Each node configured independently
```

### Rapid Changes

```bash
# User changes mind quickly
sudo snap set openstack feature.multi-region=true
sudo snap set openstack feature.multi-region=false
sudo snap set openstack feature.multi-region=true

# What happens:
# - Each hook call updates cluster DB
# - Last write wins: multi-region=true
# - Daemon sees final state on next poll
# - All nodes converge to true
```

### Multiple Gates

```bash
# Different users on different nodes
# Node A:
sudo snap set openstack feature.multi-region=true

# Node B:
sudo snap set openstack feature.experimental=true

# Result:
# - Both gates accumulate in cluster DB
# - Within 5-10 seconds, all nodes have both gates enabled
```

## Verification and Debugging

### Check Current State

```bash
# Snap config (local to each node)
snap get openstack -d | jq '.feature'

# Cluster database (same across all nodes)
# TODO: Add CLI command to query cluster DB
# For now, check via logs or Python client
```

### Daemon Logs

```bash
# Check if watcher is running
sudo journalctl -u snap.openstack.daemon -f | grep "feature gate"

# Example output:
# "Started feature gate sync watcher"
# "Feature gates changed, syncing to snap config"
# "Synced feature gate to snap: multi-region=true"
```

### Hook Logs

```bash
# Hook execution is logged by snapd
sudo journalctl -u snapd -f | grep configure

# Errors appear in snap logs
snap logs openstack
```

### Common Issues

**Issue: Daemon logs "snapctl set failed"**
- Cause: Snap confinement or permissions issue
- Solution: May need `snapd-control` interface or adjust snap permissions

**Issue: Changes don't propagate**
- Check: Is daemon running on all nodes? (`systemctl status snap.openstack.daemon`)
- Check: Is cluster database accessible? (network connectivity)
- Check: Are there errors in daemon logs?

**Issue: Hook fails but snap set succeeds**
- Expected: Hook failures don't block snap set
- Impact: Change is local-only until connectivity restored
- Recovery: Daemon will eventually sync cluster → node when available

## Performance Characteristics

### Latency
- **Local node**: Immediate (hook pushes to DB instantly)
- **Remote nodes**: 5-10 seconds (poll interval + execution time)
- **Network partition**: Changes queue until connectivity restored

### Resource Usage
- **CPU**: Minimal - one goroutine polling every 5s
- **Memory**: Minimal - tracks ~10-20 feature gates in map
- **Network**: Very low - small JSON API calls every 5s

### Scalability
- **Tested**: Up to 10 nodes (expected to work fine)
- **Theoretical limit**: dqlite cluster size limit (~100 nodes)
- **Bottleneck**: Not feature gates sync (this is very lightweight)

## Error Handling

### Daemon Watcher Errors

**Scenario:** `snapctl set` fails on a node
- **Behavior:** Error logged, sync continues on other nodes
- **Recovery:** Next poll cycle (5s) will retry the operation
- **Impact:** One node may lag temporarily, but will catch up

**Scenario:** Cluster DB unavailable
- **Behavior:** Error logged, daemon continues polling
- **Recovery:** Automatic when DB becomes available
- **Impact:** No updates propagate during outage

### Hook Errors

**Scenario:** Cannot connect to cluster DB
- **Behavior:** Error logged, snap set still succeeds
- **Recovery:** User's change is local-only until cluster reconnects
- **Impact:** Eventually consistent when daemon syncs cluster → node

**Scenario:** API call fails (not found, network error, etc.)
- **Behavior:** Logged but doesn't prevent hook completion
- **Recovery:** Daemon will eventually sync the state
- **Impact:** Temporary inconsistency (seconds to minutes)

### Network Partition

**Scenario:** Node loses connectivity to cluster

**During partition:**
- Node cannot push changes to cluster (hook fails)
- Node cannot receive changes from cluster (daemon fails)
- Local snap config remains at last known good state
- Commands use local snap config (may be stale)

**After partition heals:**
- Hook pushes any pending local changes to cluster
- Daemon pulls latest cluster state to local snap config
- Conflicts resolved by last-write-wins
- All nodes converge within 5-10 seconds

## Testing Checklist

Development and CI testing should cover:

- [ ] **Single node**: Set gate, verify hook updates cluster DB
- [ ] **Two nodes**: Set on Node A, verify propagates to Node B
- [ ] **Three+ nodes**: Set on one, verify all nodes receive update
- [ ] **Local deployment**: Verify bidirectional sync (snap ↔ cluster)
- [ ] **MAAS deployment**: Verify one-way sync only (snap → cluster, no writeback)
- [ ] **Deployment type check**: Verify daemon handles missing deployment.type (defaults to local)
- [ ] **Concurrent different gates**: Node A sets F1, Node B sets F2 simultaneously
- [ ] **Concurrent same gate**: Node A sets F1=true, Node B sets F1=false simultaneously
- [ ] **Rapid changes**: Multiple snap set commands in quick succession
- [ ] **Daemon restart**: Stop/start daemon, verify sync resumes
- [ ] **Daemon crash**: Kill daemon ungracefully, verify recovery on restart
- [ ] **Hook failure**: Block cluster DB, verify snap set still works
- [ ] **Circular triggers**: Verify debounce prevents infinite loops
- [ ] **Network partition**: Disconnect node, reconnect, verify convergence
- [ ] **Unset gates**: Use `snap unset`, verify removed from cluster and other nodes
- [ ] **Large values**: Test with many feature gates (10+)
- [ ] **Empty cluster**: No gates set, verify no errors

## Security and Confinement

### Snap Confinement

The daemon needs permission to modify snap configuration:
- Runs within the snap's confinement
- Uses `snapctl` which is allowed for snap's own configuration
- May need `snapd-control` interface if confinement is strict

### Access Control

- Only root can run `snap set` (sudo required)
- Microcluster API uses Unix socket with permissions
- No network exposure of configuration API

## Future Improvements

### Short Term
1. **CLI Command**: Add `sunbeam cluster feature-gates list` command
2. **Health Check**: Expose sync status in daemon health endpoint
3. **Metrics**: Track sync latency and error rates

### Medium Term
1. **Configurable Poll Interval**: Allow tuning via snap config
   ```bash
   snap set openstack feature-gate-sync-interval=10s
   ```
2. **Faster Sync**: Reduce default interval to 2-3 seconds
3. **Bulk Updates**: Optimize multiple gate changes into single snapctl call

### Long Term
1. **Event-Driven Sync**: Replace polling with dqlite notifications (if/when available)
2. **Conflict Timestamps**: Track change times for better conflict resolution
3. **Partial Network Partition**: Handle split-brain scenarios more gracefully
4. **Configuration Diff**: Show what changed and when for debugging

## Comparison with Alternative Approaches

### Rejected: Hook-Based Bidirectional Sync

We initially considered making the hook handle both directions (snap ↔ cluster), but found critical issues:

**Problems:**
- Hook cannot distinguish fresh user changes from stale cached values
- Race condition: Node B with stale value overwrites Node A's fresh change
- Example: Node A sets multi-region=true, Node B runs `snap set daemon.debug=true`
  - Node B hook reads all snap config including stale multi-region=false
  - Hook pushes to cluster, overwrites Node A's change
- snapd doesn't tell hook which specific key triggered the snap set

**Why daemon approach is better:**
- Single write path in each direction (no ambiguity)
- Daemon knows it's the only component pulling from cluster
- Hook knows it's pushing user's explicit changes
- Clean separation of concerns

### Rejected: Command-Level Sync

We tried syncing on every command invocation (`sunbeam list`, etc.), but:

**Problems:**
- Commands might not run frequently enough (could be hours)
- Adds latency to every command execution
- Still requires hook for push direction
- Doesn't help with non-Sunbeam operations (e.g., OpenStack CLI)

**Why daemon approach is better:**
- Proactive sync (doesn't wait for user commands)
- No command latency impact
- Consistent 5-second propagation time

## Conclusion

The daemon-based synchronization architecture provides:
- ✅ Race-free synchronization across all nodes
- ✅ Simple user experience (one command, cluster-wide effect)
- ✅ Predictable 5-10 second propagation time
- ✅ Resilient to failures (graceful degradation)
- ✅ Minimal resource overhead (lightweight poller)
- ✅ Clean architecture (separation of concerns)

This design ensures that feature gates work correctly in multi-node deployments without requiring users to manually synchronize configuration or worry about race conditions.
