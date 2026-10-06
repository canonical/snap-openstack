# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Release upgrade coordinator.

The coordinator is the central integration point for the upgrade engine.
It ties together:
- the advisory lock with fencing token (clusterd)
- the typed state model
- the orchestration metadata
- the error code catalog
- the release tracks table

It is generic: it knows the lifecycle pattern (lock, load state, load
metadata, dispatch to phase handlers, persist state, release lock) but
nothing about specific releases, charms, or actions. Those live in the
metadata and the phase handlers.

Phase handlers (preflight, control-plane, dataplane, storage, finalize)
implement the PhaseHandler protocol. The coordinator calls them; they
read their config from the metadata and read/write state via the
coordinator's persist_state method.
"""

from __future__ import annotations

import logging
import os
import threading
import typing
from dataclasses import dataclass
from enum import Enum

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.models import AcquireUpgradeLockResponse
from sunbeam.clusterd.service import UpgradeTokenMismatchException
from sunbeam.upgrades.errors import UpgradeErrorCode
from sunbeam.upgrades.metadata import HopMetadata, load_upgrade_metadata
from sunbeam.upgrades.observability import UpgradeLogger
from sunbeam.upgrades.state import (
    Hop,
    HopStatus,
    LastError,
    PhaseStatus,
    UpgradeState,
)
from sunbeam.versions import is_valid_hop

LOG = logging.getLogger(__name__)


class PhaseName(str, Enum):
    """The five phases of a hop, in order."""

    PREFLIGHT = "preflight"
    CONTROL_PLANE = "control_plane"
    DATAPLANE = "dataplane"
    STORAGE = "storage"
    FINALIZE = "finalize"


@dataclass
class PhaseResult:
    """Result of running a phase handler."""

    success: bool
    error_code: UpgradeErrorCode | None = None
    error_message: str | None = None


class PhaseHandler(typing.Protocol):
    """Protocol for phase handlers (W3-W6 implement this).

    A phase handler reads its configuration from the HopMetadata, reads
    and writes state via the coordinator, and returns a PhaseResult. The
    coordinator is generic — it doesn't know what the handler does, only
    that it follows this interface.
    """

    def run(
        self,
        coordinator: ReleaseUpgradeCoordinator,
        metadata: HopMetadata | None,
        state: UpgradeState,
    ) -> PhaseResult:
        """Execute the phase. Return success or failure with error code."""
        ...


# Valid state transitions per component (section 6.2 of the design).
# The coordinator checks every transition against this table.
VALID_HOP_TRANSITIONS: dict[HopStatus, set[HopStatus]] = {
    HopStatus.PENDING: {HopStatus.IN_PROGRESS, HopStatus.ABANDONED},
    HopStatus.IN_PROGRESS: {
        HopStatus.COMPLETED,
        HopStatus.BLOCKED,
        HopStatus.FAILED,
        HopStatus.ABANDONED,
    },
    HopStatus.BLOCKED: {HopStatus.ABANDONED, HopStatus.IN_PROGRESS},
    HopStatus.FAILED: {HopStatus.ABANDONED},
    HopStatus.COMPLETED: set(),
    HopStatus.ABANDONED: set(),
}

VALID_PHASE_TRANSITIONS: dict[PhaseStatus, set[PhaseStatus]] = {
    PhaseStatus.PENDING: {PhaseStatus.IN_PROGRESS},
    PhaseStatus.IN_PROGRESS: {
        PhaseStatus.COMPLETED,
        PhaseStatus.FAILED,
        PhaseStatus.BLOCKED,
    },
    PhaseStatus.FAILED: {PhaseStatus.IN_PROGRESS},
    PhaseStatus.BLOCKED: {PhaseStatus.IN_PROGRESS},
    PhaseStatus.COMPLETED: set(),
}


class TransitionError(Exception):
    """Raised when a state transition is invalid."""

    def __init__(
        self,
        component: str,
        current: str,
        attempted: str,
    ):
        super().__init__(f"invalid {component} transition: {current} -> {attempted}")
        self.component = component
        self.current = current
        self.attempted = attempted


class ReleaseUpgradeCoordinator:
    """Coordinates a major-release upgrade hop.

    Lifecycle:
        1. acquire_lock(holder_id) -> token
        2. load_state() -> UpgradeState
        3. load_metadata(target_release) -> HopMetadata
        4. validate_hop(from, to)
        5. run_phase(phase_name, handler) -> PhaseResult
        6. persist_state() after each step
        7. release_lock()

    The coordinator holds the fencing token for the duration of the
    command. Every state write goes through persist_state(), which calls
    clusterd's update_upgrade_state with the token. A stale token (lock
    expired and re-acquired) surfaces as UpgradeTokenMismatchException.

    Resume: on any command, load_state() reads the persisted state. If
    a hop is in progress, the coordinator finds the current phase and
    step. Completed steps are skipped (via is_step_complete). Steps with
    status in_progress are treated as failed and re-run from scratch.
    """

    def __init__(self, client: Client, logger: UpgradeLogger | None = None):
        self.client = client
        self._token: int | None = None
        self._state: UpgradeState | None = None
        self._metadata: HopMetadata | None = None
        self.logger = logger or UpgradeLogger()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()

    @property
    def token(self) -> int | None:
        """Return the current fencing token, or None if no lock is held."""
        return self._token

    @property
    def state(self) -> UpgradeState | None:
        """Return the loaded upgrade state, or None if not yet loaded."""
        return self._state

    @property
    def metadata(self) -> HopMetadata | None:
        """Return the loaded orchestration metadata, or None if not yet loaded."""
        return self._metadata

    def acquire_lock(self, holder_id: str | None = None) -> int:
        """Acquire the advisory lock. Returns the fencing token.

        Starts a background heartbeat thread that refreshes the lock's
        TTL every 30 seconds (TTL is 60s in clusterd).

        :param holder_id: identifies the process. Defaults to hostname+pid.
        :raises UpgradeLockHeldException: if another live holder owns it.
        """
        if holder_id is None:
            holder_id = f"{os.uname().nodename}-{os.getpid()}"
        response: AcquireUpgradeLockResponse = self.client.cluster.acquire_upgrade_lock(
            holder_id
        )
        self._token = response.token
        LOG.info("acquired upgrade lock (token=%d, holder=%s)", self._token, holder_id)
        self.logger.log_lock_event("acquired", self._token, holder_id)
        self._start_heartbeat()
        return self._token

    def _start_heartbeat(self) -> None:
        """Start the background heartbeat thread."""
        self._heartbeat_stop.clear()

        def _beat() -> None:
            while not self._heartbeat_stop.wait(30):
                try:
                    self.refresh_lock()
                except Exception as e:
                    LOG.warning("lock heartbeat failed: %s", e)
                    break

        self._heartbeat_thread = threading.Thread(
            target=_beat, daemon=True, name="upgrade-lock-heartbeat"
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        """Stop the background heartbeat thread."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None

    def refresh_lock(self) -> None:
        """Extend the lock's TTL. Called by the heartbeat loop.

        :raises UpgradeTokenMismatchException: if the token is stale.
        """
        if self._token is None:
            raise RuntimeError("no lock held")
        self.client.cluster.refresh_upgrade_lock(self._token)
        self.logger.log_lock_event("refreshed", self._token)

    def release_lock(self) -> None:
        """Release the lock. Safe to call even if already released."""
        self._stop_heartbeat()
        if self._token is not None:
            try:
                self.client.cluster.release_upgrade_lock(self._token)
                LOG.info("released upgrade lock (token=%d)", self._token)
                self.logger.log_lock_event("released", self._token)
            except UpgradeTokenMismatchException:
                LOG.warning(
                    "lock token %d is stale — already re-acquired by another process",
                    self._token,
                )
                self.logger.log_lock_event("stale", self._token)
            finally:
                self._token = None

    def load_state(self) -> UpgradeState:
        """Load persisted state from clusterd. Returns empty state if none."""
        state_json = self.client.cluster.get_upgrade_state()
        if state_json is None:
            self._state = UpgradeState()
            LOG.info("no existing upgrade state — fresh start")
        else:
            self._state = UpgradeState.model_validate_json(state_json)
            LOG.info(
                "loaded upgrade state: active_hop=%s, hops=%d",
                self._state.active_hop.hop_history_index,
                len(self._state.hop_history),
            )
        return self._state

    def persist_state(self) -> None:
        """Persist the current state to clusterd (CAS-guarded by token).

        :raises UpgradeTokenMismatchException: if the token is stale.
        :raises RuntimeError: if no lock or state is loaded.
        """
        if self._token is None:
            raise RuntimeError("no lock held — call acquire_lock first")
        if self._state is None:
            raise RuntimeError("no state loaded — call load_state first")
        self.client.cluster.update_upgrade_state(
            self._token,
            self._state.model_dump_json(by_alias=True),
        )

    def load_metadata(self, target_release: str) -> HopMetadata:
        """Load orchestration metadata for the target release.

        :param target_release: e.g. "2026.1"
        :raises FileNotFoundError: if the metadata file does not exist.
        """
        self._metadata = load_upgrade_metadata(target_release)
        LOG.info(
            "loaded metadata for %s -> %s (%d groups, %d finalize steps)",
            self._metadata.from_release,
            self._metadata.to_release,
            len(self._metadata.control_plane_groups),
            len(self._metadata.finalize),
        )
        return self._metadata

    def validate_hop(self, from_release: str, to_release: str) -> None:
        """Validate that from->to is a supported upgrade hop.

        :raises ValueError: if the hop is not a valid upgrade path.
        """
        if not is_valid_hop(from_release, to_release):
            raise ValueError(
                f"invalid upgrade hop: {from_release} -> {to_release}. "
                "Check RELEASE_TRACKS and SLURP_HOPS in versions.py."
            )
        LOG.info("validated hop: %s -> %s", from_release, to_release)

    def create_hop(
        self,
        from_release: str,
        to_release: str,
        metadata_build_id: str,
    ) -> Hop:
        """Create a new hop in persisted state.

        Called by preflight after all checks pass and backups are
        confirmed. Writes the initial state: active_hop points to a new
        entry in hop_history with status pending.

        :param from_release: source release
        :param to_release: target release
        :param metadata_build_id: snap revision
        :returns: the newly created Hop
        """
        if self._state is None:
            raise RuntimeError("no state loaded")
        self.validate_hop(from_release, to_release)

        hop = Hop.model_validate(
            {
                "from": from_release,
                "to": to_release,
                "metadata_version": 1,
                "metadata_build_id": metadata_build_id,
            }
        )
        index = len(self._state.hop_history)
        self._state.hop_history.append(hop)
        self._state.active_hop.hop_history_index = index
        self.persist_state()
        LOG.info("created hop %s -> %s at index %d", from_release, to_release, index)
        return hop

    def get_current_hop(self) -> Hop | None:
        """Return the active hop, or None if no hop is in flight."""
        if self._state is None:
            return None
        return self._state.current_hop

    def resume(self) -> tuple[PhaseName | None, str | None]:
        """Determine what to resume.

        Loads persisted state and finds the current phase and step. If no
        hop is active, returns (None, None). If a hop is active, returns
        the phase name and a description of where to resume.

        :returns: (phase_name, step_description) or (None, None)
        """
        if self._state is None:
            self.load_state()

        hop = self.get_current_hop()
        if hop is None or hop.status not in (
            HopStatus.IN_PROGRESS,
            HopStatus.BLOCKED,
        ):
            return (None, None)

        phase = hop.phase
        if phase is None:
            return (None, None)

        try:
            phase_name = PhaseName(phase)
        except ValueError:
            return (None, None)

        if phase_name == PhaseName.DATAPLANE:
            step = self._find_resume_step_dataplane(hop)
        elif phase_name == PhaseName.CONTROL_PLANE:
            step = self._find_resume_step_control_plane(hop)
        else:
            step = None

        return (phase_name, step)

    def _find_resume_step_dataplane(self, hop: Hop) -> str | None:
        """Find the first non-completed node/step in the dataplane phase."""
        dataplane = hop.phases.dataplane
        for node_name, node in dataplane.nodes.items():
            if node.status != PhaseStatus.COMPLETED:
                if node.step and node.step_status.value == "in_progress":
                    return f"node {node_name}: re-run step '{node.step}'"
                if node.step:
                    return f"node {node_name}: step '{node.step}'"
                return f"node {node_name}"
        return None

    def _find_resume_step_control_plane(self, hop: Hop) -> str | None:
        """Find the first non-completed group in the control-plane phase."""
        control_plane = hop.phases.control_plane
        for group_name, group in control_plane.groups.items():
            if group.status != PhaseStatus.COMPLETED:
                return f"group {group_name}"
        return None

    def run_phase(
        self,
        phase_name: PhaseName,
        handler: PhaseHandler,
    ) -> PhaseResult:
        """Run a phase handler.

        Transitions the phase to in_progress, dispatches to the handler,
        persists state, and transitions to completed/failed based on the
        result.

        :param phase_name: which phase to run
        :param handler: the phase handler implementation
        :returns: PhaseResult
        """
        if self._state is None:
            raise RuntimeError("state must be loaded first")

        hop = self.get_current_hop()
        if hop is None:
            raise RuntimeError("no active hop")

        phase_obj = getattr(hop.phases, phase_name.value)
        self._transition_phase(phase_obj, PhaseStatus.IN_PROGRESS)
        hop.phase = phase_name.value
        self.logger.log_state_change(
            "phase", phase_name.value, "phase_started", "in_progress"
        )
        self.persist_state()

        try:
            result = handler.run(self, self._metadata, self._state)
        except Exception as e:
            LOG.exception("phase %s raised: %s", phase_name.value, e)
            self.logger.log_state_change(
                "phase",
                phase_name.value,
                "phase_exception",
                "failed",
                error_message=str(e),
            )
            result = PhaseResult(
                success=False,
                error_code=UpgradeErrorCode.HOP_INVALID_TRANSITION,
                error_message=str(e),
            )

        if result.success:
            self._transition_phase(phase_obj, PhaseStatus.COMPLETED)
            self.logger.log_state_change(
                "phase", phase_name.value, "phase_completed", "completed"
            )
        else:
            self._transition_phase(phase_obj, PhaseStatus.FAILED)
            self.logger.log_state_change(
                "phase",
                phase_name.value,
                "phase_failed",
                "failed",
                error_code=result.error_code.value if result.error_code else None,
                error_message=result.error_message,
            )
            if result.error_code:
                phase_obj.last_error = LastError(
                    code=result.error_code.value,
                    message=result.error_message or "",
                )

        self.persist_state()
        return result

    def _transition_phase(
        self,
        phase_obj: typing.Any,
        new_status: PhaseStatus,
    ) -> None:
        """Validate and apply a phase state transition.

        :raises TransitionError: if the transition is not in the valid set.
        """
        current = phase_obj.status
        if new_status not in VALID_PHASE_TRANSITIONS.get(current, set()):
            raise TransitionError("phase", current.value, new_status.value)
        phase_obj.status = new_status

    def _transition_hop(
        self,
        hop: Hop,
        new_status: HopStatus,
    ) -> None:
        """Validate and apply a hop state transition.

        :raises TransitionError: if the transition is not in the valid set.
        """
        current = hop.status
        if new_status not in VALID_HOP_TRANSITIONS.get(current, set()):
            raise TransitionError("hop", current.value, new_status.value)
        hop.status = new_status

    def abandon(self) -> None:
        """Abandon the current hop.

        Marks the hop as abandoned, releases the lock. Restore artifacts
        are retained (the operator can still restore from backup).
        Prints the restore procedure to stdout.
        """
        if self._state is None:
            raise RuntimeError("no state loaded")

        hop = self.get_current_hop()
        if hop is None:
            raise RuntimeError("no active hop to abandon")

        self._transition_hop(hop, HopStatus.ABANDONED)
        self._state.active_hop.hop_history_index = None
        self.persist_state()
        self.logger.log_state_change("hop", "active_hop", "hop_abandoned", "abandoned")
        self.release_lock()
        LOG.info("hop abandoned")
