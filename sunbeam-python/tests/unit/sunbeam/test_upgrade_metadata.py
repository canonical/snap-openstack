# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the upgrade metadata schema and loader."""

from pathlib import Path

import pytest
import yaml

from sunbeam.upgrades.metadata import (
    ActionScope,
    ActionSpec,
    ComputeConfig,
    DataplaneConfig,
    FinalizeStep,
    HopMetadata,
    StepType,
    StorageConfig,
    load_upgrade_metadata,
)

MANIFEST_DIR = Path(__file__).resolve().parents[4] / "manifests"


class TestSchemaValidation:
    """Schema validation: required fields, type checks, defaults."""

    def test_minimal_hop(self):
        hop = HopMetadata.model_validate(
            {
                "from": "2024.1",
                "to": "2025.1",
                "control_plane_groups": [
                    {"name": "identity-core", "apps": ["keystone-k8s"]}
                ],
            }
        )
        assert hop.from_release == "2024.1"
        assert hop.to_release == "2025.1"
        assert len(hop.control_plane_groups) == 1
        assert hop.control_plane_groups[0].ready_timeout_sec == 600
        assert hop.dataplane.compute.principal == "openstack-hypervisor"
        assert "epa-orchestrator" in hop.dataplane.compute.auxiliary

    def test_action_spec_defaults_to_leader(self):
        action_spec = ActionSpec(action="pre-upgrade", apps=["keystone-k8s"])
        assert action_spec.scope == ActionScope.LEADER

    def test_finalize_action_step_requires_action_and_apps(self):
        with pytest.raises(Exception):
            FinalizeStep(name="test", type=StepType.ACTION)

    def test_finalize_action_step_validates(self):
        step = FinalizeStep(
            name="rpc-cache-refresh",
            type=StepType.ACTION,
            action="rpc-cache-refresh",
            apps=["nova-k8s"],
            scope=ActionScope.ALL_UNITS,
        )
        assert step.action == "rpc-cache-refresh"
        assert step.scope == ActionScope.ALL_UNITS

    def test_finalize_engine_step(self):
        step = FinalizeStep(
            name="reapply-terraform",
            type=StepType.ENGINE,
        )
        assert step.action is None
        assert step.apps is None

    def test_dataplane_steps_default(self):
        config = DataplaneConfig(
            compute=ComputeConfig(principal="openstack-hypervisor")
        )
        assert "resolve" in config.steps
        assert "mark-complete" in config.steps
        assert len(config.steps) == 9

    def test_storage_steps_default(self):
        config = StorageConfig(principal="cinder-volume")
        assert "refresh-snap" in config.steps
        assert len(config.steps) == 5


class TestLoadUpgradeMetadata:
    """Loader: reads the shipped 2025.1 upgrade.yml."""

    def test_loads_2026_1_upgrade_metadata(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        assert hop.from_release == "2025.1"
        assert hop.to_release == "2026.1"

    def test_2026_1_has_8_control_plane_groups(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        group_names = [g.name for g in hop.control_plane_groups]
        assert group_names == [
            "identity-core",
            "image",
            "placement",
            "block-storage-api",
            "network-api",
            "compute-control",
            "dashboard",
            "optional-features",
        ]

    def test_compute_control_has_longer_timeout(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        compute_control = next(
            g for g in hop.control_plane_groups if g.name == "compute-control"
        )
        assert compute_control.ready_timeout_sec == 900

    def test_optional_features_has_9_apps(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        features = next(
            g for g in hop.control_plane_groups if g.name == "optional-features"
        )
        assert len(features.apps) == 9

    def test_finalize_has_rpc_cache_refresh_on_all_units(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        rpc_step = next(s for s in hop.finalize if s.name == "rpc-cache-refresh")
        assert rpc_step.type == StepType.ACTION
        assert rpc_step.action == "rpc-cache-refresh"
        assert rpc_step.apps == [
            "nova-k8s",
            "openstack-hypervisor",
            "cinder-k8s",
            "cinder-volume",
        ]
        assert rpc_step.scope == ActionScope.ALL_UNITS

    def test_finalize_has_engine_steps(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        engine_steps = [s.name for s in hop.finalize if s.type == StepType.ENGINE]
        assert "verify-upgrade-levels" in engine_steps
        assert "reapply-terraform" in engine_steps
        assert "upgrade-features" in engine_steps
        assert "validate-end-state" in engine_steps

    def test_dataplane_config(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        assert hop.dataplane.compute.principal == "openstack-hypervisor"
        assert "epa-orchestrator" in hop.dataplane.compute.auxiliary
        assert "openstack-network-agents" in hop.dataplane.compute.auxiliary
        assert hop.dataplane.registration_timeout_sec == 300

    def test_storage_config(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        assert hop.storage.principal == "cinder-volume"
        assert hop.storage.registration_timeout_sec == 300

    def test_required_prerequisites(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        prereqs = hop.required_prerequisites
        snap_prereq = next(p for p in prereqs if p.type == "snap_refresh")
        assert snap_prereq.channel == "2026.1/stable"
        infra_prereqs = [p.component for p in prereqs if p.type == "infra_refresh"]
        assert "mysql" in infra_prereqs
        assert "vault" in infra_prereqs

    def test_compatibility_empty_for_2025_1(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        assert hop.compatibility.pre_hop == []
        assert hop.compatibility.post_hop == []

    def test_raises_on_missing_release(self):
        with pytest.raises(FileNotFoundError):
            load_upgrade_metadata("1999.1", manifest_dir=MANIFEST_DIR)

    def test_round_trip_serialization(self):
        """The shipped YAML must round-trip through the pydantic model."""
        raw = yaml.safe_load((MANIFEST_DIR / "2026.1" / "upgrade.yml").read_text())
        hop = HopMetadata.model_validate(raw)
        dumped = hop.model_dump(by_alias=True, exclude_none=True)
        restored = HopMetadata.model_validate(dumped)
        assert restored.model_dump(by_alias=True, exclude_none=True) == dumped


class TestGroupPrePostActions:
    """Every control-plane group has pre-upgrade + post-upgrade actions."""

    def test_all_groups_have_pre_and_post_actions(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        for group in hop.control_plane_groups:
            assert len(group.pre_actions) > 0, f"{group.name} has no pre_actions"
            assert len(group.post_actions) > 0, f"{group.name} has no post_actions"
            for action in group.pre_actions:
                assert action.action == "pre-upgrade"
                assert action.scope == ActionScope.LEADER
            for action in group.post_actions:
                assert action.action == "post-upgrade"
                assert action.scope == ActionScope.LEADER

    def test_pre_action_apps_match_group_apps(self):
        hop = load_upgrade_metadata("2026.1", manifest_dir=MANIFEST_DIR)
        for group in hop.control_plane_groups:
            for action in group.pre_actions:
                assert set(action.apps) == set(group.apps), (
                    f"{group.name}: pre_action apps {action.apps}"
                    f" != group apps {group.apps}"
                )
