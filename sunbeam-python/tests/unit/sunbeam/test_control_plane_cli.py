# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the control-plane CLI command."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sunbeam.clusterd.models import AcquireUpgradeLockResponse
from sunbeam.clusterd.service import UpgradeLockHeldException
from sunbeam.commands.upgrade import upgrade
from sunbeam.upgrades.metadata import HopMetadata

FROM = "2025.1"
TO = "2026.1"


def _metadata():
    return HopMetadata.model_validate(
        {
            "from": FROM,
            "to": TO,
            "control_plane_groups": [
                {
                    "name": "identity-core",
                    "apps": ["keystone-k8s"],
                    "ready_timeout_sec": 600,
                    "terraform_targets": {"keystone-k8s": ["module.keystone"]},
                    "pre_actions": [
                        {
                            "action": "pre-upgrade",
                            "apps": ["keystone-k8s"],
                            "scope": "leader",
                        }
                    ],
                    "post_actions": [
                        {
                            "action": "post-upgrade",
                            "apps": ["keystone-k8s"],
                            "scope": "leader",
                        }
                    ],
                },
                {
                    "name": "image",
                    "apps": ["glance-k8s"],
                    "ready_timeout_sec": 600,
                    "terraform_targets": {"glance-k8s": ["module.glance"]},
                },
            ],
        }
    )


def _patch_meta():
    return patch(
        "sunbeam.commands.upgrade.load_upgrade_metadata",
        return_value=_metadata(),
    )


def _state_json(groups=None):
    if groups is None:
        groups = {}
    return json.dumps(
        {
            "active_hop": {"hop_history_index": 0},
            "hop_history": [
                {
                    "from": FROM,
                    "to": TO,
                    "metadata_version": 1,
                    "metadata_build_id": "123",
                    "status": "in_progress",
                    "phase": "control_plane",
                    "phases": {
                        "preflight": {"status": "completed"},
                        "control_plane": {
                            "status": "in_progress",
                            "groups": groups,
                        },
                        "dataplane": {"status": "pending", "nodes": {}},
                        "storage": {"status": "pending", "nodes": {}},
                        "finalize": {"status": "pending"},
                    },
                }
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
    client.cluster.get_upgrade_state.return_value = _state_json()
    client.cluster.update_upgrade_state.return_value = None
    client.cluster.release_upgrade_lock.return_value = None
    deployment.get_client.return_value = client

    tfhelper = MagicMock()
    manifest = MagicMock()
    jhelper = MagicMock()

    # Set up model status with apps matching metadata charm names
    status = MagicMock()
    keystone_app = MagicMock()
    keystone_app.charm = "keystone-k8s"
    glance_app = MagicMock()
    glance_app.charm = "glance-k8s"
    status.apps = {"keystone": keystone_app, "glance": glance_app}
    jhelper.get_model_status.return_value = status

    jhelper.get_leader_unit.return_value = "keystone/0"
    jhelper.run_action.return_value = {"result": "ok"}
    deployment.get_tfhelper.return_value = tfhelper
    deployment.get_manifest.return_value = manifest
    deployment.get_juju_helper.return_value = jhelper
    return deployment


class TestControlPlaneStatus:
    def test_status_shows_groups(self, mock_deployment):
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade, ["control-plane", "--status"], obj=mock_deployment
            )
        assert result.exit_code == 0
        assert "identity-core" in result.output
        assert "image" in result.output

    def test_status_shows_completed(self, mock_deployment):
        client = mock_deployment.get_client.return_value
        client.cluster.get_upgrade_state.return_value = _state_json(
            groups={
                "identity-core": {
                    "status": "completed",
                    "completed_at": "2025-01-01T00:00:00Z",
                }
            }
        )
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade, ["control-plane", "--status"], obj=mock_deployment
            )
        assert result.exit_code == 0
        assert "completed" in result.output

    def test_status_no_active_hop(self, mock_deployment):
        client = mock_deployment.get_client.return_value
        client.cluster.get_upgrade_state.return_value = None
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["control-plane", "--status"], obj=mock_deployment
        )
        assert result.exit_code != 0
        assert "no active" in result.output.lower()


class TestControlPlaneDryRun:
    def test_dry_run_shows_plan(self, mock_deployment):
        tfhelper = mock_deployment.get_tfhelper.return_value
        tfhelper.update_partial_tfvars_and_plan_tf.return_value = [
            {"@level": "warning", "@message": "keystone-k8s: channel will change"},
        ]
        tfhelper.terraform_plan_text.return_value = (
            "# keystone will be updated in-place\n"
            '  ~ channel = "2025.1/edge" -> "2026.1/edge/upgrade"'
        )
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade, ["control-plane", "--dry-run"], obj=mock_deployment
            )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "identity-core" in result.output
        assert "keystone-k8s" in result.output
        assert "Plan changes:" in result.output

    def test_dry_run_with_group_filter(self, mock_deployment):
        tfhelper = mock_deployment.get_tfhelper.return_value
        tfhelper.update_partial_tfvars_and_plan_tf.return_value = []
        tfhelper.terraform_plan_text.return_value = ""
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                ["control-plane", "--dry-run", "--group", "identity-core"],
                obj=mock_deployment,
            )
        assert result.exit_code == 0
        assert "identity-core" in result.output
        assert "image" not in result.output

    def test_dry_run_shows_actions(self, mock_deployment):
        tfhelper = mock_deployment.get_tfhelper.return_value
        tfhelper.update_partial_tfvars_and_plan_tf.return_value = []
        tfhelper.terraform_plan_text.return_value = ""
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade, ["control-plane", "--dry-run"], obj=mock_deployment
            )
        assert result.exit_code == 0
        assert "Pre: pre-upgrade" in result.output
        assert "Post: post-upgrade" in result.output


class TestControlPlaneAuto:
    def test_auto_upgrades_all(self, mock_deployment):
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade, ["control-plane", "--auto"], obj=mock_deployment
            )
        assert result.exit_code == 0
        assert "completed" in result.output.lower()

    def test_auto_lock_held(self, mock_deployment):
        client = mock_deployment.get_client.return_value
        client.cluster.acquire_upgrade_lock.side_effect = UpgradeLockHeldException(
            "held"
        )
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade, ["control-plane", "--auto"], obj=mock_deployment
            )
        assert result.exit_code != 0
        assert "lock" in result.output.lower()


class TestControlPlaneGroup:
    def test_group_upgrades_single(self, mock_deployment):
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                ["control-plane", "--group", "identity-core"],
                obj=mock_deployment,
            )
        assert result.exit_code == 0

    def test_group_not_found(self, mock_deployment):
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                ["control-plane", "--group", "nonexistent"],
                obj=mock_deployment,
            )
        assert result.exit_code != 0


class TestControlPlaneApplication:
    def test_application_upgrades_single(self, mock_deployment):
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                ["control-plane", "--application", "keystone"],
                obj=mock_deployment,
            )
        assert result.exit_code == 0

    def test_application_rejected_on_failed(self, mock_deployment):
        client = mock_deployment.get_client.return_value
        client.cluster.get_upgrade_state.return_value = _state_json(
            groups={
                "identity-core": {
                    "status": "failed",
                    "last_error": {"code": "TEST", "message": "fail"},
                }
            }
        )
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                ["control-plane", "--application", "keystone"],
                obj=mock_deployment,
            )
        assert result.exit_code != 0
        assert "failed" in result.output.lower()


class TestControlPlaneRetryGroup:
    def test_retry_failed_group(self, mock_deployment):
        client = mock_deployment.get_client.return_value
        client.cluster.get_upgrade_state.return_value = _state_json(
            groups={
                "identity-core": {
                    "status": "failed",
                    "last_error": {"code": "TEST", "message": "fail"},
                }
            }
        )
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                ["control-plane", "--retry-group", "identity-core"],
                obj=mock_deployment,
            )
        assert result.exit_code == 0

    def test_retry_not_failed(self, mock_deployment):
        client = mock_deployment.get_client.return_value
        client.cluster.get_upgrade_state.return_value = _state_json(
            groups={
                "identity-core": {
                    "status": "completed",
                    "completed_at": "2025-01-01T00:00:00Z",
                }
            }
        )
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                ["control-plane", "--retry-group", "identity-core"],
                obj=mock_deployment,
            )
        assert result.exit_code != 0
        assert "not failed" in result.output.lower()


class TestFlagValidation:
    def test_group_and_app_mutually_exclusive(self, mock_deployment):
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                [
                    "control-plane",
                    "--group",
                    "identity-core",
                    "--application",
                    "keystone-k8s",
                ],
                obj=mock_deployment,
            )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_auto_and_group_mutually_exclusive(self, mock_deployment):
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(
                upgrade,
                ["control-plane", "--auto", "--group", "identity-core"],
                obj=mock_deployment,
            )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_no_flags_requires_one(self, mock_deployment):
        runner = CliRunner()
        with _patch_meta():
            result = runner.invoke(upgrade, ["control-plane"], obj=mock_deployment)
        assert result.exit_code != 0
        assert "required" in result.output.lower()
