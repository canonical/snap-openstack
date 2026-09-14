# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the preflight CLI command."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sunbeam.clusterd.models import AcquireUpgradeLockResponse
from sunbeam.clusterd.service import UpgradeLockHeldException
from sunbeam.commands.upgrade import upgrade
from sunbeam.upgrades.metadata import HopMetadata
from sunbeam.upgrades.preflight.capacity import CapacityPolicy

FROM = "2025.1"
TO = "2026.1"


def _make_app(current="active", charm_channel="2025.1/stable"):
    app = MagicMock()
    app.app_status.current = current
    app.app_status.message = ""
    app.charm_channel = charm_channel
    return app


def _model_status(apps):
    status = MagicMock()
    status.apps = apps
    return status


def _make_metadata():
    return HopMetadata.model_validate(
        {
            "from": FROM,
            "to": TO,
            "control_plane_groups": [
                {"name": "identity-core", "apps": ["keystone-k8s"]}
            ],
        }
    )


@pytest.fixture
def mock_deployment():
    deployment = MagicMock()
    client = MagicMock()
    client.cluster.acquire_upgrade_lock.return_value = AcquireUpgradeLockResponse(
        token=1
    )
    client.cluster.get_upgrade_state.return_value = None
    client.cluster.update_upgrade_state.return_value = None
    client.cluster.release_upgrade_lock.return_value = None
    client.cluster.update_config.return_value = None
    deployment.get_client.return_value = client

    jhelper = MagicMock()
    jhelper.get_model_status.side_effect = [
        _model_status(
            {
                "keystone-k8s": _make_app("active", "2025.1/stable"),
                "nova-k8s": _make_app("active", "2025.1/stable"),
                "mysql-k8s": _make_app("active", "8.0/stable"),
            }
        ),
        _model_status(
            {
                "nova-compute": _make_app("active"),
                "openstack-network-agents": _make_app("active"),
            }
        ),
    ]
    jhelper.get_leader_unit.return_value = "mysql-k8s/0"
    jhelper.run_action.return_value = {"cluster-status": "ok"}
    jhelper.get_application.return_value = MagicMock(
        units={"openstack-hypervisor/0": MagicMock()}
    )
    deployment.get_juju_helper.return_value = jhelper
    return deployment


def _preflight_patches(**overrides):
    """Common patches for preflight tests. All checks pass by default."""
    defaults = {
        "snap_release": TO,
        "metadata": _make_metadata(),
        "snap_revision": "123",
        "capacity_policy": CapacityPolicy(),
    }
    defaults.update(overrides)
    return [
        patch(
            "sunbeam.commands.upgrade.detect_snap_release",
            return_value=defaults["snap_release"],
        ),
        patch(
            "sunbeam.upgrades.preflight.checks.detect_snap_release",
            return_value=defaults["snap_release"],
        ),
        patch(
            "sunbeam.upgrades.preflight.checks.load_upgrade_metadata",
            return_value=defaults["metadata"],
        ),
        patch(
            "sunbeam.upgrades.preflight.hop._get_snap_revision",
            return_value=defaults["snap_revision"],
        ),
        patch(
            "sunbeam.upgrades.preflight.capacity.load_capacity_policy",
            return_value=defaults["capacity_policy"],
        ),
    ]


class TestPreflightCommand:
    def test_preflight_success_creates_hop(self, mock_deployment):
        runner = CliRunner()
        for p in _preflight_patches():
            p.start()
        try:
            result = runner.invoke(
                upgrade, ["preflight", "--from", FROM], obj=mock_deployment
            )
        finally:
            patch.stopall()
        assert result.exit_code == 0
        assert "All checks passed" in result.output
        assert "Active hop created" in result.output

    def test_preflight_auto_detects_from(self, mock_deployment):
        runner = CliRunner()
        patches = _preflight_patches()
        patches.append(
            patch("sunbeam.commands.upgrade._detect_from_release", return_value=FROM)
        )
        for p in patches:
            p.start()
        try:
            result = runner.invoke(upgrade, ["preflight"], obj=mock_deployment)
        finally:
            patch.stopall()
        assert result.exit_code == 0
        assert f"{FROM} -> {TO}" in result.output

    def test_preflight_fails_on_stale_snap(self, mock_deployment):
        runner = CliRunner()
        for p in _preflight_patches(snap_release=FROM):
            p.start()
        try:
            result = runner.invoke(
                upgrade, ["preflight", "--from", FROM], obj=mock_deployment
            )
        finally:
            patch.stopall()
        assert result.exit_code != 0
        assert "exit 2" in result.output

    def test_preflight_capacity_override_flag(self, mock_deployment):
        runner = CliRunner()
        for p in _preflight_patches():
            p.start()
        try:
            result = runner.invoke(
                upgrade,
                ["preflight", "--from", FROM, "--capacity-policy-override"],
                obj=mock_deployment,
            )
        finally:
            patch.stopall()
        assert result.exit_code == 0

    def test_preflight_lock_held(self, mock_deployment):
        client = mock_deployment.get_client.return_value
        client.cluster.acquire_upgrade_lock.side_effect = UpgradeLockHeldException(
            "held"
        )
        runner = CliRunner()
        for p in _preflight_patches():
            p.start()
        try:
            result = runner.invoke(
                upgrade, ["preflight", "--from", FROM], obj=mock_deployment
            )
        finally:
            patch.stopall()
        assert result.exit_code != 0
        assert "lock" in result.output.lower()

    def test_preflight_prints_backup_note(self, mock_deployment):
        runner = CliRunner()
        for p in _preflight_patches():
            p.start()
        try:
            result = runner.invoke(
                upgrade, ["preflight", "--from", FROM], obj=mock_deployment
            )
        finally:
            patch.stopall()
        assert result.exit_code == 0
        assert "sunbeam backup" in result.output

    def test_preflight_prints_next_step(self, mock_deployment):
        runner = CliRunner()
        for p in _preflight_patches():
            p.start()
        try:
            result = runner.invoke(
                upgrade, ["preflight", "--from", FROM], obj=mock_deployment
            )
        finally:
            patch.stopall()
        assert result.exit_code == 0
        assert "control-plane" in result.output


class TestDetectFromRelease:
    def test_auto_detect_fails_without_charm_channels(self, mock_deployment):
        # Override jhelper to return empty model status so
        # detect_deployed_release returns None
        jhelper = mock_deployment.get_juju_helper.return_value
        jhelper.get_model_status.side_effect = None
        jhelper.get_model_status.return_value = _model_status({})
        runner = CliRunner()
        result = runner.invoke(upgrade, ["preflight"], obj=mock_deployment)
        assert result.exit_code != 0
        assert "auto-detect" in result.output.lower()
