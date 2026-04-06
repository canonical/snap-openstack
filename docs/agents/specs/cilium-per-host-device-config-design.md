# Per-Host Cilium Device Configuration via CiliumNodeConfig

## Problem

The cluster-wide `k8sd/v1alpha1/cilium/devices` annotation uses generic device
name patterns (e.g., `eth+`, `bond+`, `br-bond+`) to tell Cilium which network
interfaces to use. Because the pattern matching is broad, Cilium binds itself to
wrong devices.

Excluding OVS bridges (`!br-ex`, `!br-int`) is insufficient: Cilium still
matches the physical NIC that serves as the OVS uplink port inside the bridge.
For example, `enp1s0f1` is the physnet1 OVS uplink inside `br-ex`, and it gets
matched by the `enp+` pattern. Cilium attaches BPF programs
(`cil_from_netdev`/`cil_to_netdev`) to that NIC in the TC ingress chain, running
before OVS's `rx_handler`. When Cilium reprograms those BPF programs (e.g.,
during `UpdatePolicyMaps` after a pod event), external traffic — such as VLAN
packets destined for floating IPs — gets dropped.

The exclusion-list approach is a losing game: every hardware configuration can
have different NIC names serving as OVS uplinks, and there is no reliable pattern
to exclude them all. A surgical approach is needed: tell Cilium exactly which
device to use on each host, rather than trying to enumerate what to avoid.

## Solution

Replace the cluster-wide cilium devices annotation with per-host
`CiliumNodeConfig` resources that specify the exact interface name for each
control node. The interface is determined by querying juju for each machine's
network interfaces and matching against the internal space.

## Reverts

Revert the following commits (newest-first):

1. `f001990c` — fix: stop cilium greedy match on br+
2. `2be63d7b` — fix: exclude well-known ovs bridges / devices
3. `46273728` — feat: bind k8s cluster endpoint to internal space with Cilium
   device detection

This removes:

- `CILIUM_DEVICES_ANNOTATION_DEFAULT` and `CILIUM_DEVICES_ANNOTATION_KEY`
- The `cluster-annotations` cilium devices config in `_get_k8s_config_tfvars()`
- The comma-separated validation logic for cilium devices
- The `"cluster"` endpoint binding to `Networks.INTERNAL` in `extra_tfvars()`
- `EnsureCiliumOnCorrectSpaceStep` class
- The rename of `_get_management_ips` → `_get_cluster_ips` in
  `EnsureK8SUnitsTaggedStep`
- All references in `intra_channel.py`
- Associated tests

The cluster endpoint stays on management.

## Base Class: `_PerHostK8SResourceStep`

Extract the common per-host logic shared between `EnsureL2AdvertisementByHostStep`
and the new cilium step into `_PerHostK8SResourceStep(BaseStep)` (private,
underscore-prefixed). This deduplicates ~60-70 lines.

### Provides

- **Constructor**: `deployment`, `client`, `jhelper`, `model`, `network`,
  `fqdn` (optional). Initializes `to_update: list[dict]`,
  `to_delete: list[dict]`, `_ifnames: dict[str, str]` cache.
- **`_get_interface(node)`**: Looks up machine interfaces via
  `jhelper.get_machine_interfaces()`, matches by the step's `network` space.
  Caches results in `_ifnames`. Raises a step-specific error (defined by
  subclass) if no matching interface found.
- **`is_skip(context)`**: Control node lookup (single node if `fqdn` provided,
  all control nodes otherwise), kube client creation, calls abstract
  `_get_outdated_resources(nodes, kube)`, populates `to_update`/`to_delete`,
  returns SKIPPED if empty.

### Subclasses must implement

- **`_get_outdated_resources(nodes, kube)`** → `(outdated, deleted)` lists
- **`run(context)`** — resource-specific apply/delete logic

### Refactor `EnsureL2AdvertisementByHostStep`

Refactor to inherit from `_PerHostK8SResourceStep` instead of `BaseStep`.
Moves `_get_interface`, control node lookup, and kube client creation into the
base. L2-specific logic (`_ensure_l2_advertisement`, `_labels`, pool handling,
tenacity retry on webhook failures) stays in the subclass.

## New Step: `EnsureCiliumDeviceByHostStep`

Inherits from `_PerHostK8SResourceStep` with
`network=Networks.INTERNAL`.

### `_get_outdated_resources(nodes, kube)`

Lists existing `CiliumNodeConfig` resources labeled with
`app.kubernetes.io/managed-by: <deployment>`. For each, checks:

- Hostname label matches a known node
- `spec.nodeSelector.matchLabels` contains exactly
  `sunbeam/hostname: <hostname>` (guards against over-broad or empty
  selectors that could silently misconfigure other nodes)
- `spec.defaults.devices` matches the current internal-space interface for that
  node

A resource that has the right metadata label and device but a wrong or missing
`nodeSelector` is considered outdated and will be re-applied.

Returns `(outdated, deleted)` lists.

### `run(context)`

- For each node in `to_update`: look up interface, apply `CiliumNodeConfig` via
  lightkube, then delete the cilium pod on that node to trigger restart
- For each node in `to_delete`: delete the `CiliumNodeConfig` resource and
  delete the cilium pod
- Returns COMPLETED or FAILED

### CiliumNodeConfig Resource

```yaml
apiVersion: cilium.io/v2
kind: CiliumNodeConfig
metadata:
  name: cilium-devices-<hostname>
  namespace: kube-system
  labels:
    app.kubernetes.io/managed-by: <deployment-name>
    kubernetes.io/hostname: <hostname>
spec:
  nodeSelector:
    matchLabels:
      kubernetes.io/hostname: <hostname>
  defaults:
    devices: "<interface-name>"
```

Uses `cilium.io/v2` (Canonical K8s 1.32 LTS ships Cilium 1.17.1; `v2alpha1`
is deprecated since Cilium 1.17).

### Cilium Pod Restart

After applying a config change for a node, restart the cilium agent on that node:

1. **Find** the cilium pod on the affected node: filter by `spec.nodeName` in
   `kube-system` namespace, label `k8s-app=cilium`. If no pod is found, return
   FAILED (config was applied but cannot take effect without a running agent).
2. **Delete** the pod. The DaemonSet controller recreates it with the new
   config.
3. **Wait for readiness**: poll until a new cilium pod exists on that node and
   reports Ready (`status.conditions` has `type=Ready, status=True`). Use a
   timeout consistent with the existing `_ROLLOUT_TIMEOUT` (300s) with 5-second
   polling intervals. If the timeout expires, return FAILED.

Only pods on modified nodes are restarted. The readiness wait ensures the
datapath is operational before the plan proceeds to MetalLB/L2 steps.

## Lightkube Generic Resource

Define a lightkube generic resource for `CiliumNodeConfig` via a `K8SHelper`
class method (same pattern as `get_lightkube_l2_advertisement_resource()`),
using `lightkube.generic_resource.create_namespaced_resource` with:

- group: `cilium.io`
- version: `v2`
- kind: `CiliumNodeConfig`
- plural: `ciliumnodeconfigs`

## Integration into Command Plans

`EnsureCiliumDeviceByHostStep` is placed **before**
`EnsureL2AdvertisementByHostStep` in every plan (cilium must be correctly
configured before metallb).

### MAAS commands (`sunbeam-python/sunbeam/provider/maas/commands.py`)

- Bootstrap plan — before `EnsureL2AdvertisementByHostStep`, all control nodes
- Remove-node plan — all control nodes (cleans up departed node's config)

### Local commands (`sunbeam-python/sunbeam/provider/local/commands.py`)

- Bootstrap plan — same positioning
- Add-node — with `fqdn`
- Resize plan — with `fqdn`

### Upgrade steps (`sunbeam-python/sunbeam/steps/upgrades/intra_channel.py`)

- Replaces `EnsureCiliumOnCorrectSpaceStep` references
- Operates on all control nodes (no `fqdn`)

## Tests

Existing `TestEnsureL2AdvertisementByHostStep` tests must keep passing after the
refactor to `_PerHostK8SResourceStep`. No new tests needed for the base class
itself — it is covered through its concrete subclasses.

`TestEnsureCiliumDeviceByHostStep` in
`tests/unit/sunbeam/steps/test_k8s.py`:

- `test_is_skip_no_changes` — all configs up to date, returns SKIPPED
- `test_is_skip_outdated_device` — device mismatch, returns COMPLETED
- `test_is_skip_missing_config` — node has no config yet, returns COMPLETED
- `test_is_skip_deleted_node` — config exists for removed node, returns
  COMPLETED
- `test_is_skip_single_node_fqdn` — fqdn mode operates on single node only
- `test_is_skip_wrong_node_selector` — config has correct label/device but
  wrong or empty `nodeSelector`, returns COMPLETED (outdated)
- `test_run_creates_config` — applies CiliumNodeConfig, deletes cilium pod,
  waits for Ready replacement
- `test_run_updates_config` — updates existing config, restarts affected pod
  only
- `test_run_deletes_stale_config` — removes config for departed node, deletes
  pod
- `test_run_api_error` — lightkube API failure returns FAILED
- `test_run_no_interface_found` — no interface in internal space, returns FAILED
- `test_run_cilium_pod_not_found` — no cilium pod on target node, returns
  FAILED
- `test_run_restart_timeout` — replacement pod never becomes Ready, returns
  FAILED

Mocking: `jhelper.get_machine_interfaces`, `client.cluster.list_nodes_by_role`,
lightkube client operations — same fixtures as
`TestEnsureL2AdvertisementByHostStep`.
