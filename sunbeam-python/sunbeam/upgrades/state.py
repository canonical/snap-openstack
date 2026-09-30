# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Typed state model for the release-upgrade engine.

Models the persisted ``upgrade_state`` JSON blob:

- ``metadata_build_id`` is a typed field on ``Hop``, sourced from the
  snap revision at preflight. It lets the engine detect a mid-hop snap refresh
  and validate engine compatibility against the persisted metadata version.

- ``active_hop`` is a reference (``hop_history_index``), not a duplicate
  of the hop's live state. ``hop_history[index]`` is the single canonical
  record — one source of truth, no dual-write drift. ``active_hop`` is ``None``
  when no hop is in flight.

The lock primitive serializes writes to this blob via
``ClusterService.update_upgrade_state(token, state_json)``. The entire blob is
read and written as a whole — no partial updates — so SIGKILL during a write
leaves either the old or the new state, never a split.
"""

from __future__ import annotations

import enum

import pydantic


class HopStatus(str, enum.Enum):
    """Lifecycle state of a single upgrade hop."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    ABANDONED = "abandoned"


class PhaseStatus(str, enum.Enum):
    """Lifecycle state of a phase within a hop."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class StepStatus(str, enum.Enum):
    """Lifecycle state of a single step within a node upgrade."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ComponentRole(str, enum.Enum):
    """Role of a juju unit within a node upgrade.

    AUXILIARY means a co-located principal charm on the same machine that
    must be refreshed alongside the principal unit (e.g. epa-orchestrator,
    openstack-network-agents). Not a Juju subordinate — subordinates ride
    their principal's refresh automatically via relation machinery.
    """

    PRINCIPAL = "principal"
    AUXILIARY = "auxiliary"


class ComponentStatus(str, enum.Enum):
    """Lifecycle state of a single component (unit) within a node upgrade."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class LastError(pydantic.BaseModel):
    """Error snapshot persisted on a failed phase/group/node/step.

    code comes from the error code catalog. message is human-readable
    detail. Both are surfaced by ``sunbeam cluster upgrade status`` so
    the operator can act without a separate command.
    """

    code: str = pydantic.Field(description="Error code from the catalog.")
    message: str = pydantic.Field(description="Human-readable error detail.")


class Component(pydantic.BaseModel):
    """A single juju unit being upgraded as part of a node."""

    unit: str = pydantic.Field(description="Juju unit name, e.g. nova-k8s/0")
    role: ComponentRole = pydantic.Field(description="principal or auxiliary")
    previous_channel: str = pydantic.Field(
        description="Channel before this hop, e.g. 2024.1/stable"
    )
    target_channel: str = pydantic.Field(
        description="Channel for this hop, e.g. 2025.1/stable"
    )
    status: ComponentStatus = pydantic.Field(
        default=ComponentStatus.PENDING, description="Upgrade status of this unit"
    )


class Node(pydantic.BaseModel):
    """A single compute or storage node being upgraded in the data plane.

    Carries the per-node step state machine. The coordinator writes ``step``
    before executing it (SIGKILL safety — on resume, a step with
    ``status: in_progress`` is treated as failed and re-executed).

    ``auxiliary_units`` are co-located principal charms on the same machine
    that must be refreshed alongside the principal (e.g. epa-orchestrator
    and openstack-network-agents alongside openstack-hypervisor on a compute
    node). They are NOT Juju subordinates — a true subordinate
    (e.g. cinder-volume-ceph) rides its principal's refresh automatically
    via Juju relation machinery and needs no explicit step here.
    """

    status: PhaseStatus = pydantic.Field(
        default=PhaseStatus.PENDING, description="Node upgrade status"
    )
    step: str | None = pydantic.Field(
        default=None,
        description="Current or last-attempted step name within the node sequence",
    )
    step_status: StepStatus = pydantic.Field(
        default=StepStatus.PENDING,
        description="Status of the current step (SIGKILL safety)",
    )
    principal_unit: str | None = pydantic.Field(
        default=None, description="Principal juju unit for this node"
    )
    auxiliary_units: list[str] = pydantic.Field(
        default_factory=list, description="Auxiliary juju units for this node"
    )
    components: list[Component] = pydantic.Field(
        default_factory=list, description="Per-unit upgrade records"
    )
    last_error: LastError | None = pydantic.Field(
        default=None, description="Error snapshot if status is failed"
    )


class Group(pydantic.BaseModel):
    """A control-plane upgrade group (e.g. identity-core, compute-control)."""

    status: PhaseStatus = pydantic.Field(
        default=PhaseStatus.PENDING, description="Group upgrade status"
    )
    started_at: str | None = pydantic.Field(
        default=None, description="ISO timestamp when the group started"
    )
    completed_at: str | None = pydantic.Field(
        default=None, description="ISO timestamp when the group completed"
    )
    last_error: LastError | None = pydantic.Field(
        default=None, description="Error snapshot if status is failed"
    )


class ControlPlanePhase(pydantic.BaseModel):
    """Control-plane phase: a set of groups upgraded in sequence."""

    status: PhaseStatus = pydantic.Field(
        default=PhaseStatus.PENDING, description="Phase status"
    )
    groups: dict[str, Group] = pydantic.Field(
        default_factory=dict,
        description="Per-group state, keyed by group name from metadata",
    )


class DataplanePhase(pydantic.BaseModel):
    """Data-plane phase: per-node compute upgrades."""

    status: PhaseStatus = pydantic.Field(
        default=PhaseStatus.PENDING, description="Phase status"
    )
    nodes: dict[str, Node] = pydantic.Field(
        default_factory=dict,
        description="Per-node state, keyed by hostname or juju unit name",
    )


class SimplePhase(pydantic.BaseModel):
    """A phase with no internal sub-structure (preflight, storage, finalize).

    Used for phases that track only a status + optional backup_id or
    last_error, without groups or nodes.
    """

    status: PhaseStatus = pydantic.Field(
        default=PhaseStatus.PENDING, description="Phase status"
    )
    backup_id: str | None = pydantic.Field(
        default=None,
        description="Backup artifact ID (preflight phase only)",
    )
    last_error: LastError | None = pydantic.Field(
        default=None, description="Error snapshot if status is failed"
    )


class Phases(pydantic.BaseModel):
    """All phases of a hop."""

    preflight: SimplePhase = pydantic.Field(default_factory=SimplePhase)
    control_plane: ControlPlanePhase = pydantic.Field(default_factory=ControlPlanePhase)
    dataplane: DataplanePhase = pydantic.Field(default_factory=DataplanePhase)
    storage: SimplePhase = pydantic.Field(default_factory=SimplePhase)
    finalize: SimplePhase = pydantic.Field(default_factory=SimplePhase)


class Hop(pydantic.BaseModel):
    """A single release-to-release upgrade hop.

    This is the canonical record — ``active_hop`` in the top-level state is
    just a reference (``hop_history_index``) into the ``hop_history`` list.
    All hop state lives here.
    """

    from_release: str = pydantic.Field(
        alias="from",
        description="Source release, e.g. 2024.1",
    )
    to_release: str = pydantic.Field(
        alias="to",
        description="Target release, e.g. 2025.1",
    )
    status: HopStatus = pydantic.Field(
        default=HopStatus.PENDING, description="Hop lifecycle status"
    )
    phase: str | None = pydantic.Field(
        default=None,
        description="Current phase: preflight, control_plane, dataplane,"
        " storage, or finalize",
    )
    metadata_version: int = pydantic.Field(
        description="Metadata schema version this hop was created with"
    )
    metadata_build_id: str = pydantic.Field(
        description="Snap revision that created this hop."
        " Detects mid-hop snap refresh.",
    )
    phases: Phases = pydantic.Field(
        default_factory=Phases, description="Per-phase state"
    )
    last_error: LastError | None = pydantic.Field(
        default=None, description="Error snapshot if the hop is failed"
    )


class ActiveHop(pydantic.BaseModel):
    """Reference to the active hop in ``hop_history``.

    ``hop_history_index`` is the index into the ``hop_history`` list. ``None``
    means no hop is in flight. This is the ONLY field — the hop's status,
    phase, and all detail live in ``hop_history[index]`` to avoid dual-write
    drift.
    """

    hop_history_index: int | None = pydantic.Field(
        default=None,
        description="Index into hop_history for the active hop,"
        " or None if no hop is active",
    )


class UpgradeState(pydantic.BaseModel):
    """Top-level persisted upgrade state.

    Stored in clusterd under the ``upgrade_state`` config key as JSON. Every
    write is guarded by the fencing token via
    ``ClusterService.update_upgrade_state(token, state_json)``.

    ``active_hop`` is a reference, not a duplicate of the hop's live
    state. ``hop_history[active_hop.hop_history_index]`` is canonical.
    """

    active_hop: ActiveHop = pydantic.Field(default_factory=ActiveHop)
    hop_history: list[Hop] = pydantic.Field(default_factory=list)

    @property
    def current_hop(self) -> Hop | None:
        """Return the active hop, or None if no hop is in flight."""
        idx = self.active_hop.hop_history_index
        if idx is None:
            return None
        if idx < 0 or idx >= len(self.hop_history):
            return None
        return self.hop_history[idx]

    def is_upgrade_active(self) -> bool:
        """True if a hop is in progress (active and not terminal)."""
        hop = self.current_hop
        if hop is None:
            return False
        return hop.status in (
            HopStatus.PENDING,
            HopStatus.IN_PROGRESS,
            HopStatus.BLOCKED,
        )

    def is_step_complete(self, phase: str, step: str) -> bool:
        """Check if a step within the current hop's phase is complete.

        Used by the coordinator's resume logic to skip completed steps.
        Only meaningful for dataplane/storage node steps — control-plane
        groups use the ``Group`` status directly.
        """
        hop = self.current_hop
        if hop is None:
            return False
        phase_obj = getattr(hop.phases, phase, None)
        if phase_obj is None:
            return False
        if phase == "dataplane":
            # Node-level steps: check all nodes' step_status
            return all(
                node.step_status == StepStatus.COMPLETED
                for node in phase_obj.nodes.values()
            )
        if hasattr(phase_obj, "status"):
            return phase_obj.status == PhaseStatus.COMPLETED
        return False

    def mark_step_complete(self, phase: str, step: str) -> None:
        """Mark a step within the current hop's phase as complete.

        For dataplane: marks the given step as complete on all nodes that
        have that step as their current step. For other phases: marks the
        phase itself as completed. Caller is responsible for persisting the
        state via ``update_upgrade_state`` after mutation.
        """
        hop = self.current_hop
        if hop is None:
            raise ValueError("no active hop")
        phase_obj = getattr(hop.phases, phase, None)
        if phase_obj is None:
            raise ValueError(f"unknown phase: {phase}")
        if phase == "dataplane":
            for node in phase_obj.nodes.values():
                if node.step == step:
                    node.step_status = StepStatus.COMPLETED
        else:
            phase_obj.status = PhaseStatus.COMPLETED
