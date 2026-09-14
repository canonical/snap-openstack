# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the upgrade state model (G2 + G3 resolutions)."""

import copy

import pytest

from sunbeam.upgrades.state import (
    Hop,
    HopStatus,
    PhaseStatus,
    StepStatus,
    UpgradeState,
)

STATE_EXAMPLE = {
    "active_hop": {"hop_history_index": 0},
    "hop_history": [
        {
            "from": "2024.1",
            "to": "2025.1",
            "status": "in_progress",
            "phase": "dataplane",
            "metadata_version": 1,
            "metadata_build_id": "snap-rev-1005",
            "phases": {
                "preflight": {
                    "status": "completed",
                    "backup_id": "backup-20250805-0900",
                },
                "control_plane": {
                    "status": "completed",
                    "groups": {
                        "identity-core": {
                            "status": "completed",
                            "started_at": "2025-08-05T09:00:00Z",
                            "completed_at": "2025-08-05T09:05:00Z",
                        },
                        "compute-control": {"status": "completed"},
                    },
                },
                "dataplane": {
                    "status": "in_progress",
                    "nodes": {
                        "compute-17": {
                            "status": "failed",
                            "step": "finish-node-upgrade",
                            "step_status": "failed",
                            "principal_unit": "openstack-hypervisor/17",
                            "auxiliary_units": [
                                "epa-orchestrator/17",
                                "openstack-network-agents/17",
                            ],
                            "components": [
                                {
                                    "unit": "openstack-hypervisor/17",
                                    "role": "principal",
                                    "previous_channel": "2024.1/stable",
                                    "target_channel": "2025.1/stable",
                                    "status": "failed",
                                },
                                {
                                    "unit": "epa-orchestrator/17",
                                    "role": "auxiliary",
                                    "previous_channel": "2024.1/stable",
                                    "target_channel": "2025.1/stable",
                                    "status": "pending",
                                },
                            ],
                            "last_error": {
                                "code": "SERVICE_REGISTRATION_TIMEOUT",
                                "message": "nova-compute did not re-register within 300s",
                            },
                        },
                        "compute-18": {"status": "pending"},
                    },
                },
                "storage": {"status": "pending"},
                "finalize": {"status": "pending"},
            },
        }
    ],
}


class TestUpgradeStateRoundTrip:
    """The state example must deserialize and re-serialize losslessly."""

    def test_parses_example(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        assert state.active_hop.hop_history_index == 0
        assert len(state.hop_history) == 1

    def test_round_trip_preserves_state(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        json_str = state.model_dump_json(by_alias=True)
        restored = UpgradeState.model_validate_json(json_str)
        assert restored.model_dump(by_alias=True) == state.model_dump(by_alias=True)

    def test_serializes_to_expected_shape(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        dumped = state.model_dump(by_alias=True, exclude_none=True)
        # G3: active_hop is just an index, not a duplicate of the hop's state
        assert dumped["active_hop"] == {"hop_history_index": 0}
        # G2: metadata_build_id is present on the hop
        assert dumped["hop_history"][0]["metadata_build_id"] == "snap-rev-1005"
        # The hop's status/phase live ONLY in hop_history, not in active_hop
        assert "status" not in dumped["active_hop"]
        assert "phase" not in dumped["active_hop"]


class TestG3OneSourceOfTruth:
    """active_hop is a reference; hop_history[index] is canonical."""

    def test_current_hop_returns_the_active_hop(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        hop = state.current_hop
        assert hop is not None
        assert hop.from_release == "2024.1"
        assert hop.to_release == "2025.1"
        assert hop.status == HopStatus.IN_PROGRESS

    def test_current_hop_returns_none_when_no_active_hop(self):
        state = UpgradeState()
        assert state.current_hop is None

    def test_current_hop_returns_none_when_index_out_of_range(self):
        state = UpgradeState(
            active_hop={"hop_history_index": 5},
            hop_history=[],
        )
        assert state.current_hop is None

    def test_is_upgrade_active_true_when_in_progress(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        assert state.is_upgrade_active() is True

    def test_is_upgrade_active_false_when_no_hop(self):
        state = UpgradeState()
        assert state.is_upgrade_active() is False

    def test_is_upgrade_active_false_when_completed(self):
        data = copy.deepcopy(STATE_EXAMPLE)
        data["hop_history"][0]["status"] = "completed"
        state = UpgradeState.model_validate(data)
        assert state.is_upgrade_active() is False


class TestG2MetadataBuildId:
    """metadata_build_id is a typed field, sourced from snap revision."""

    def test_metadata_build_id_round_trips(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        assert state.current_hop.metadata_build_id == "snap-rev-1005"
        json_str = state.model_dump_json(by_alias=True)
        assert "snap-rev-1005" in json_str

    def test_metadata_build_id_is_required_on_hop(self):
        """A hop without metadata_build_id should fail validation."""
        bad_hop = {
            "from": "2024.1",
            "to": "2025.1",
            "status": "in_progress",
            "metadata_version": 1,
            # metadata_build_id missing
        }
        with pytest.raises(Exception):
            Hop.model_validate(bad_hop)


class TestIdempotencyHelpers:
    """is_step_complete / mark_step_complete for resume logic."""

    def test_is_step_complete_false_for_in_progress_phase(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        # dataplane is in_progress — not complete
        assert state.is_step_complete("dataplane", "finish-node-upgrade") is False

    def test_is_step_complete_true_for_completed_phase(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        assert state.is_step_complete("control_plane", "identity-core") is True

    def test_mark_step_complete_sets_node_step_status(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        hop = state.current_hop
        # compute-18 is pending with no step
        node = hop.phases.dataplane.nodes["compute-18"]
        node.step = "disable-scheduling"
        node.step_status = StepStatus.IN_PROGRESS
        state.mark_step_complete("dataplane", "disable-scheduling")
        assert node.step_status == StepStatus.COMPLETED

    def test_mark_step_complete_raises_when_no_active_hop(self):
        state = UpgradeState()
        with pytest.raises(ValueError, match="no active hop"):
            state.mark_step_complete("dataplane", "some-step")

    def test_mark_step_complete_raises_for_unknown_phase(self):
        state = UpgradeState.model_validate(STATE_EXAMPLE)
        with pytest.raises(ValueError, match="unknown phase"):
            state.mark_step_complete("nonexistent", "some-step")


class TestEmptyState:
    """A fresh UpgradeState with no hop should be safe to query."""

    def test_empty_state_serializes(self):
        state = UpgradeState()
        json_str = state.model_dump_json(by_alias=True)
        restored = UpgradeState.model_validate_json(json_str)
        assert restored.current_hop is None
        assert restored.is_upgrade_active() is False

    def test_empty_state_current_hop_is_none(self):
        state = UpgradeState()
        assert state.current_hop is None

    def test_empty_state_is_step_complete_is_false(self):
        state = UpgradeState()
        assert state.is_step_complete("dataplane", "any-step") is False


class TestHopCreation:
    """W3.4 (hop creation) will construct a fresh Hop + UpgradeState."""

    def test_new_hop_has_correct_defaults(self):
        hop = Hop(
            **{
                "from": "2024.1",
                "to": "2025.1",
                "metadata_version": 1,
                "metadata_build_id": "snap-rev-1005",
            }
        )
        assert hop.status == HopStatus.PENDING
        assert hop.phase is None
        assert hop.phases.preflight.status == PhaseStatus.PENDING
        assert hop.phases.control_plane.status == PhaseStatus.PENDING
        assert hop.phases.dataplane.status == PhaseStatus.PENDING

    def test_new_state_with_first_hop(self):
        hop = Hop(
            **{
                "from": "2024.1",
                "to": "2025.1",
                "metadata_version": 1,
                "metadata_build_id": "snap-rev-1005",
            }
        )
        state = UpgradeState(
            active_hop={"hop_history_index": 0},
            hop_history=[hop],
        )
        assert state.current_hop is not None
        assert state.current_hop.from_release == "2024.1"
        assert state.is_upgrade_active() is True
