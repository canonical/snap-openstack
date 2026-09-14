# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the release upgrade coordinator."""

import json
from unittest.mock import MagicMock, patch

import pytest

from sunbeam.clusterd.models import AcquireUpgradeLockResponse
from sunbeam.clusterd.service import (
    UpgradeLockHeldException,
    UpgradeTokenMismatchException,
)
from sunbeam.upgrades.coordinator import (
    VALID_HOP_TRANSITIONS,
    VALID_PHASE_TRANSITIONS,
    PhaseHandler,
    PhaseName,
    PhaseResult,
    ReleaseUpgradeCoordinator,
    TransitionError,
)
from sunbeam.upgrades.errors import UpgradeErrorCode
from sunbeam.upgrades.state import (
    HopStatus,
    PhaseStatus,
)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.cluster.acquire_upgrade_lock.return_value = AcquireUpgradeLockResponse(
        token=1
    )
    client.cluster.get_upgrade_state.return_value = None
    client.cluster.update_upgrade_state.return_value = None
    client.cluster.release_upgrade_lock.return_value = None
    client.cluster.refresh_upgrade_lock.return_value = None
    return client


@pytest.fixture
def coordinator(mock_client):
    return ReleaseUpgradeCoordinator(mock_client)


class TestAcquireLock:
    def test_acquire_returns_token(self, coordinator, mock_client):
        token = coordinator.acquire_lock("host-pid")
        assert token == 1
        assert coordinator.token == 1
        mock_client.cluster.acquire_upgrade_lock.assert_called_once_with("host-pid")

    def test_acquire_defaults_holder_id(self, coordinator, mock_client):
        coordinator.acquire_lock()
        holder_id = mock_client.cluster.acquire_upgrade_lock.call_args[0][0]
        assert "-" in holder_id

    def test_acquire_propagates_lock_held(self, coordinator, mock_client):
        mock_client.cluster.acquire_upgrade_lock.side_effect = UpgradeLockHeldException(
            "held"
        )
        with pytest.raises(UpgradeLockHeldException):
            coordinator.acquire_lock()

    def test_release_lock(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.release_lock()
        mock_client.cluster.release_upgrade_lock.assert_called_once_with(1)
        assert coordinator.token is None

    def test_release_lock_safe_if_not_held(self, coordinator):
        coordinator.release_lock()
        assert coordinator.token is None

    def test_release_handles_stale_token(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        mock_client.cluster.release_upgrade_lock.side_effect = (
            UpgradeTokenMismatchException("stale")
        )
        coordinator.release_lock()
        assert coordinator.token is None

    def test_refresh_lock(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.refresh_lock()
        mock_client.cluster.refresh_upgrade_lock.assert_called_once_with(1)

    def test_refresh_without_lock_raises(self, coordinator):
        with pytest.raises(RuntimeError, match="no lock held"):
            coordinator.refresh_lock()


class TestLoadState:
    def test_load_empty_state(self, coordinator, mock_client):
        mock_client.cluster.get_upgrade_state.return_value = None
        state = coordinator.load_state()
        assert state.active_hop.hop_history_index is None
        assert len(state.hop_history) == 0

    def test_load_existing_state(self, coordinator, mock_client):
        state_json = json.dumps(
            {
                "active_hop": {"hop_history_index": 0},
                "hop_history": [
                    {
                        "from": "2025.1",
                        "to": "2026.1",
                        "status": "in_progress",
                        "metadata_version": 1,
                        "metadata_build_id": "rev-100",
                    }
                ],
            }
        )
        mock_client.cluster.get_upgrade_state.return_value = state_json
        state = coordinator.load_state()
        assert state.current_hop is not None
        assert state.current_hop.from_release == "2025.1"


class TestPersistState:
    def test_persist_writes_json(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        coordinator.persist_state()
        mock_client.cluster.update_upgrade_state.assert_called_once()
        args = mock_client.cluster.update_upgrade_state.call_args
        assert args[0][0] == 1
        assert "active_hop" in args[0][1]

    def test_persist_without_lock_raises(self, coordinator):
        coordinator.load_state()
        with pytest.raises(RuntimeError, match="no lock held"):
            coordinator.persist_state()

    def test_persist_without_state_raises(self, coordinator):
        coordinator.acquire_lock("test")
        with pytest.raises(RuntimeError, match="no state loaded"):
            coordinator.persist_state()

    def test_persist_propagates_token_mismatch(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        mock_client.cluster.update_upgrade_state.side_effect = (
            UpgradeTokenMismatchException("stale")
        )
        with pytest.raises(UpgradeTokenMismatchException):
            coordinator.persist_state()


class TestValidateHop:
    def test_valid_hop(self, coordinator):
        coordinator.validate_hop("2025.1", "2026.1")

    def test_invalid_hop(self, coordinator):
        with pytest.raises(ValueError, match="invalid upgrade hop"):
            coordinator.validate_hop("2024.1", "2026.1")


class TestCreateHop:
    def test_create_hop(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        hop = coordinator.create_hop("2025.1", "2026.1", "rev-100")
        assert hop.from_release == "2025.1"
        assert hop.to_release == "2026.1"
        assert hop.status == HopStatus.PENDING
        assert coordinator.state.active_hop.hop_history_index == 0
        mock_client.cluster.update_upgrade_state.assert_called_once()

    def test_create_invalid_hop_raises(self, coordinator):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        with pytest.raises(ValueError):
            coordinator.create_hop("2024.1", "2026.1", "rev-100")


class TestResume:
    def test_resume_no_active_hop(self, coordinator, mock_client):
        mock_client.cluster.get_upgrade_state.return_value = None
        phase, step = coordinator.resume()
        assert phase is None
        assert step is None

    def test_resume_completed_hop(self, coordinator, mock_client):
        state_json = json.dumps(
            {
                "active_hop": {"hop_history_index": 0},
                "hop_history": [
                    {
                        "from": "2025.1",
                        "to": "2026.1",
                        "status": "completed",
                        "metadata_version": 1,
                        "metadata_build_id": "rev-100",
                    }
                ],
            }
        )
        mock_client.cluster.get_upgrade_state.return_value = state_json
        phase, step = coordinator.resume()
        assert phase is None

    def test_resume_dataplane_phase(self, coordinator, mock_client):
        state_json = json.dumps(
            {
                "active_hop": {"hop_history_index": 0},
                "hop_history": [
                    {
                        "from": "2025.1",
                        "to": "2026.1",
                        "status": "in_progress",
                        "phase": "dataplane",
                        "metadata_version": 1,
                        "metadata_build_id": "rev-100",
                        "phases": {
                            "dataplane": {
                                "status": "in_progress",
                                "nodes": {
                                    "compute-0": {
                                        "status": "failed",
                                        "step": "refresh-principal",
                                        "step_status": "failed",
                                    }
                                },
                            }
                        },
                    }
                ],
            }
        )
        mock_client.cluster.get_upgrade_state.return_value = state_json
        phase, step = coordinator.resume()
        assert phase == PhaseName.DATAPLANE
        assert "compute-0" in step

    def test_resume_control_plane_phase(self, coordinator, mock_client):
        state_json = json.dumps(
            {
                "active_hop": {"hop_history_index": 0},
                "hop_history": [
                    {
                        "from": "2025.1",
                        "to": "2026.1",
                        "status": "in_progress",
                        "phase": "control_plane",
                        "metadata_version": 1,
                        "metadata_build_id": "rev-100",
                        "phases": {
                            "control_plane": {
                                "status": "in_progress",
                                "groups": {
                                    "identity-core": {"status": "completed"},
                                    "compute-control": {"status": "pending"},
                                },
                            }
                        },
                    }
                ],
            }
        )
        mock_client.cluster.get_upgrade_state.return_value = state_json
        phase, step = coordinator.resume()
        assert phase == PhaseName.CONTROL_PLANE
        assert "compute-control" in step


class TestRunPhase:
    def test_successful_phase(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        coordinator.create_hop("2025.1", "2026.1", "rev-100")

        handler = MagicMock(spec=PhaseHandler)
        handler.run.return_value = PhaseResult(success=True)

        result = coordinator.run_phase(PhaseName.PREFLIGHT, handler)
        assert result.success is True
        hop = coordinator.get_current_hop()
        assert hop.phases.preflight.status == PhaseStatus.COMPLETED

    def test_failed_phase_sets_error(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        coordinator.create_hop("2025.1", "2026.1", "rev-100")

        handler = MagicMock(spec=PhaseHandler)
        handler.run.return_value = PhaseResult(
            success=False,
            error_code=UpgradeErrorCode.PREFLIGHT_HEALTH_CHECK,
            error_message="ceph unhealthy",
        )

        result = coordinator.run_phase(PhaseName.PREFLIGHT, handler)
        assert result.success is False
        hop = coordinator.get_current_hop()
        assert hop.phases.preflight.status == PhaseStatus.FAILED
        assert hop.phases.preflight.last_error.code == "PREFLIGHT_HEALTH_CHECK"

    def test_handler_exception_caught(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        coordinator.create_hop("2025.1", "2026.1", "rev-100")

        handler = MagicMock(spec=PhaseHandler)
        handler.run.side_effect = RuntimeError("boom")

        result = coordinator.run_phase(PhaseName.PREFLIGHT, handler)
        assert result.success is False

    def test_run_phase_without_state_raises(self, coordinator):
        handler = MagicMock(spec=PhaseHandler)
        with pytest.raises(RuntimeError):
            coordinator.run_phase(PhaseName.PREFLIGHT, handler)


class TestTransitions:
    def test_valid_hop_transitions(self):
        assert HopStatus.IN_PROGRESS in VALID_HOP_TRANSITIONS[HopStatus.PENDING]
        assert HopStatus.COMPLETED in VALID_HOP_TRANSITIONS[HopStatus.IN_PROGRESS]

    def test_valid_phase_transitions(self):
        assert PhaseStatus.IN_PROGRESS in VALID_PHASE_TRANSITIONS[PhaseStatus.PENDING]
        assert PhaseStatus.COMPLETED in VALID_PHASE_TRANSITIONS[PhaseStatus.IN_PROGRESS]

    def test_invalid_hop_transition_raises(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        hop = coordinator.create_hop("2025.1", "2026.1", "rev-100")
        with pytest.raises(TransitionError):
            coordinator._transition_hop(hop, HopStatus.COMPLETED)

    def test_terminal_states_have_no_transitions(self):
        assert VALID_HOP_TRANSITIONS[HopStatus.COMPLETED] == set()
        assert VALID_HOP_TRANSITIONS[HopStatus.ABANDONED] == set()


class TestAbandon:
    def test_abandon_marks_hop_and_releases_lock(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        coordinator.create_hop("2025.1", "2026.1", "rev-100")

        coordinator.abandon()
        hop = coordinator.state.hop_history[0]
        assert hop.status == HopStatus.ABANDONED
        assert coordinator.state.active_hop.hop_history_index is None
        assert coordinator.token is None

    def test_abandon_without_hop_raises(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        with pytest.raises(RuntimeError, match="no active hop"):
            coordinator.abandon()


class TestLifecycle:
    """Full lifecycle: acquire, create hop, run phase, release."""

    def test_full_flow(self, coordinator, mock_client):
        coordinator.acquire_lock("test")
        coordinator.load_state()
        with patch("sunbeam.upgrades.coordinator.load_upgrade_metadata") as mock_load:
            mock_load.return_value = MagicMock(
                from_release="2025.1",
                to_release="2026.1",
                control_plane_groups=[],
                finalize=[],
            )
            coordinator.load_metadata("2026.1")
        coordinator.validate_hop("2025.1", "2026.1")
        coordinator.create_hop("2025.1", "2026.1", "rev-100")

        handler = MagicMock(spec=PhaseHandler)
        handler.run.return_value = PhaseResult(success=True)
        result = coordinator.run_phase(PhaseName.PREFLIGHT, handler)
        assert result.success is True

        coordinator.release_lock()
        assert coordinator.token is None
        assert mock_client.cluster.release_upgrade_lock.called
