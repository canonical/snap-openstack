# Cluster Release Upgrade Engine Design

## Problem

Sunbeam has no supported path to move a cluster from one OpenStack release to
the next (e.g. 2025.1 → 2026.1). Operators hand-compose charm refreshes,
terraform applies, and juju actions with no state tracking, no ordering
guarantees, and nothing stopping two cluster-mutating operations from running
at once. A failed upgrade leaves a mixed-release cluster with no record of
where it stopped.

## Architecture

### Components

| Component | Location | Responsibility |
|---|---|---|
| `sunbeam cluster upgrade` CLI | `sunbeam/commands/upgrade.py` | `preflight`, `control-plane`, `abandon` subcommands; flag validation; rendering status/plan output |
| `GuardedGroup` | `sunbeam/utils.py` | Blocks every mutating subcommand (`bootstrap`, `add`, `join`, `remove`, `resize`, `destroy`, `configure`, `refresh`, `enable`, `disable`, …) while an upgrade is active |
| Preflight | `sunbeam/upgrades/preflight/` | Sequential checks (snap version, hop validity, cluster health, capacity, MySQL quorum); also creates the active hop in state |
| Metadata loader | `sunbeam/upgrades/metadata.py` | Parses the per-release `upgrade.yml` (groups, terraform targets, actions, steps, timeouts, prerequisites) into a typed schema |
| Coordinator | `sunbeam/upgrades/coordinator.py` | Owns hop lifecycle: lock, load/save state, run a phase via a `PhaseHandler`, transition hop/phase with validity checks |
| State model | `sunbeam/upgrades/state.py` | Typed hop/phase/group/node/step tree, transition tables, `active_hop` index into `hop_history` |
| Control-plane handler | `sunbeam/upgrades/control_plane/` | Executes a group: pre-actions → manifest override → scoped terraform apply → convergence wait → post-actions; also `--retry-group` and `--status` support |
| Upgrade guard helper | `sunbeam/utils.py` | Fail-open check wrapping `clusterd.is_upgrade_active()` |
| Lock primitives | `sunbeam-microcluster/database/upgrade_lock.go` | Single-SQL-row `upgrade_lock` with monotonic fencing token; acquire / refresh / release / verify |
| Lock+state service | `sunbeam-microcluster/sunbeam/upgrade.go` | Token serialization, TTLs, and token-verify-then-write-state in one transaction |
| REST API | `sunbeam-microcluster/api/upgrade.go` | `POST/PUT/DELETE /1.0/upgrade/lock`, `GET/PUT /1.0/upgrade/state`, `GET /1.0/upgrade/active` |
| Python client | `sunbeam/clusterd/` | Acquire/refresh/release lock and get/update state; maps 404/409 to typed exceptions |
| Audit log | `sunbeam/upgrades/observability.py` | DEBUG-level structured log of state changes, lock events, and command invocations |

### Data model

`UpgradeState` is a single JSON blob in clusterd under key `upgrade_state`:

- `hop_history: [Hop]` — every hop ever started. Append-only; the canonical
  record.
- `active_hop.hop_history_index` — pointer into `hop_history`. `None` when no
  upgrade is active.
- A hop has `status`, `phase` (current phase name), `last_error`,
  `metadata_version` (1), `metadata_build_id` (snap revision at hop creation),
  and `phases` {`preflight`, `control_plane`, `dataplane`, `storage`,
  `finalize`}.
- `control_plane` phase contains `groups: dict[name → Group]` with per-group
  status + timestamps.
- `dataplane` phase (schema only for now) contains per-node `step` and
  `step_status`, plus `components` tracking previous/target charm channel per
  unit.

Transitions are enforced by `VALID_HOP_TRANSITIONS` /
`VALID_PHASE_TRANSITIONS` tables in the coordinator — `TransitionError` on
violation.

### Locking

Locking separates the *what* (state blob) from the *who* (the holder):

- The `upgrade_lock` table is always present (schema-apply inserts row id=1).
- TTL is 60 s; the CLI heartbeats every 30 s from a daemon thread.
- Acquire when held → HTTP 409; refresh/release with wrong token → 409.
- Token is never reset, so a stale holder's later writes are rejected at the
  DB — the *fencing token* pattern.
- The CLI group guard checks lock liveness (holder ≠ empty and not expired),
  not hop status: any writing CLI process holds the lock for the duration of
  its mutation, which also blocks guard-pass admission concurrently.

### Metadata-driven orchestration

`manifests/<release>/upgrade.yml` is read generically by the loader; the
coordinator and handlers execute it. Per release it declares, in order:

- `control_plane_groups`: name, apps, `ready_timeout_sec`, `pre_actions` /
  `post_actions` (`{action, apps, scope: leader|all-units}`),
  `terraform_targets: {charm → [resource addresses]}`.
- `dataplane` and `storage`: step sequences and timeout defaults (schema only
  for now).
- `finalize`: ordered steps of type `engine` or `action`.
- `required_prerequisites` (snap channel, infra components), `compatibility`
  (pre/post hop actions) — declared but not yet executed.

Adding a new release = new `upgrade.yml` + one `RELEASE_TRACKS` entry in
`versions.py`.

### Control-plane group execution

For each group (in metadata order, or via `--group` / `--application`):

1. Filter `apps` to charms actually deployed in the `openstack` model;
   resolve charm → deployed app names. Empty → success.
2. Run `pre_actions` via `juju run <app>/leader …`; on failure, post-actions
   still run as cleanup.
3. `_override_charm_manifests()` — load the target snap's embedded
   `etc/manifests/<to_release>/<risk>.yml` and replace each charm's full
   `CharmManifest` in the in-memory deployment manifest.
4. `tfhelper.init()` then `update_partial_tfvars_and_apply_tf(...,
   tf_apply_extra_args=["-target=…"])` using `terraform_targets`.
5. `wait_until_desired_status(openstack, apps, ["active"],
   timeout=ready_timeout_sec)`; `CONTROL_PLANE_CONVERGENCE_TIMEOUT` on
   failure.
6. Run `post_actions`; failure → `CONTROL_PLANE_ACTION_FAILED` (apply already
   succeeded — message notes manual intervention may be required).

`--dry-run` iterates groups and calls `plan_group()` — same tfvars update but
`update_partial_tfvars_and_plan_tf`, which does not save tfvars to the DB.

`--retry-group` requires a FAILED/BLOCKED group, resets it to PENDING, and
re-runs it. `--status` renders per-group status + timestamps.

### Failure model

- Whole-blob writes: SIGKILL leaves either old or new state.
- `in_progress` steps on resume are treated as failed and re-executed —
  steps must be idempotent (terraform apply, db_sync, snap refresh).
- Lock holder death → TTL expiry; a new holder acquires with higher token;
  dead holder's writes are rejected with 409.
- `blocked` status means operator intervention needed; `abandon` is the only
  transition out (with a pointer to `sunbeam restore`).

### Sequencing

Preflight gates all mutation and ships first. Control-plane before
dataplane: new control plane + old compute is the supported mixed-version
state; the reverse is not. Finalize (deferred integration re-apply, RPC
cache refresh, migrations) only runs after the data plane completes.

## Alternatives considered

### Execution and state model

| Alternative | Rejected because |
|---|---|
| Reconciler-based execution | Workflow-coordinator matches the operator's mental model of an ordered procedure. |
| Canonical `active_hop` record copied off `hop_history` | Keeps a dual-write problem for the hop's entire active life; index reference avoids it. |
| Per-field state updates | Needs per-field crash recovery; whole-blob writes replace all of it. |
| TTL lock without fencing token | Can't distinguish expired-then-reacquired from slow holder (GC pause); monotonic token is the standard fencing pattern. |
| Lock as a `config` key | Config table has no CAS primitive; dedicated table CAS-es the token column. |
| Canonical DataUpgrade charm library | Doesn't match Sunbeam's needs; would require a fork. |

### Terraform strategy

| Alternative | Rejected because |
|---|---|
| Full plan decomposition | Monolithic plans are sufficient; scoped `-target` per group is the lighter answer and enables precise `--retry-group`. |
| Channel-only tfvars with cascading fallback (spec v1) | Clusterd manifest holds source-release channels → plan is a silent no-op. Overriding the full `CharmManifest` from the target snap's embedded manifest is the only way the plan sees the new release. |
| Error on unknown charms | Optional features may simply not be deployed; warning+strip accepts partial deployments. |
| Reuse `.terraform` dir | Snap refresh can bump the juju provider version and stale the dir; `terraform init` before apply/plan is cheap insurance. |

### Charm interaction

| Alternative | Rejected because |
|---|---|
| `upgrade-charm` hook only | Fires on every refresh; the charm can't distinguish cross-track from in-track. Explicit `pre-upgrade`/`post-upgrade` actions fire only at hop boundaries. |
| Upgrade-mode config flag | Config is persistent and app-level — wrong semantics for transient upgrade state. |
| Drain/undrain actions | Can't control which unit restarts when (parallel pod management). |
| StatefulSet partition patching / kubectl patching | Fights Juju reconciliation; not Sunbeam-native. |

### Scope and sequencing

| Alternative | Rejected because |
|---|---|
| Guard check inside each command handler | Fragile — new commands miss it; central `GuardedGroup` intercepts at the click-group level. Fail-open on clusterd-unreachable is deliberate: CLI must not wedge. |
| Fixed convergence timeout | db_sync-heavy nova deployments exceed 10 min on first hop; timeout is per-group in metadata (default 600s, nova 900s). |
| OpenStack API capacity check | Requires admin credentials in preflight; the charm's `running-guests` action needs none. |
| Backup creation/verification in preflight | Artifacts live anywhere and restore is component-specific (MySQL/Vault); engine stays forward-only and prints a disclaimer. |
| Blue-green upgrades | Too complex for the first release. |
| Provider architecture refactor | Independent modernization, not a prerequisite. |

Juju `OrderedReady` would remove the explicit ordering actions, but is an
upstream feature request out of Sunbeam's control; the pre/post-action
separation is the foundation regardless.

## Deferred scope

- Dataplane and storage phase handlers (schema + metadata exist, no handler).
- Finalize phase handler (`verify-upgrade-levels`, `reapply-terraform`,
  `upgrade-features`, `validate-end-state`).
- `sunbeam cluster upgrade status` (only `control-plane --status`).
- `--retry-node` / `--rollback-node` flags.
- `required_prerequisites` / `compatibility.pre|post_hop` execution.
- Hop-level PENDING→IN_PROGRESS transition (only `abandon()` touches it today).
- `metadata_build_id` (snap revision) recorded but not yet validated mid-hop.
- messaging-core (rabbitmq) group commented out; no recorded rationale —
  pending decision.

## Consequences

- New release = new `upgrade.yml` + one `RELEASE_TRACKS` entry.
- Operators dry-validate before mutating (preflight checks + `control-plane
  --dry-run` terraform plan).
- Stale CLI cannot corrupt state — rejected at the DB with 409.
- Resume after SIGKILL re-executes the last in-progress step; idempotence is
  the contract.
- `--retry-group` = surgical recovery for failed control-plane groups.
- Until dataplane/finalize land, hops never reach COMPLETED.
