# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the abandon CLI command."""

import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from sunbeam.clusterd.models import AcquireUpgradeLockResponse
from sunbeam.clusterd.service import UpgradeLockHeldException
from sunbeam.commands.upgrade import upgrade


def _state_json(
    from_release: str = "2025.1",
    to_release: str = "2026.1",
    status: str = "in_progress",
) -> str:
    return json.dumps(
        {
            "active_hop": {"hop_history_index": 0},
            "hop_history": [
                {
                    "from": from_release,
                    "to": to_release,
                    "metadata_version": 1,
                    "metadata_build_id": "123",
                    "status": status,
                }
            ],
        }
    )


@pytest.fixture
def mock_deployment_no_hop():
    deployment = MagicMock()
    client = MagicMock()
    client.cluster.acquire_upgrade_lock.return_value = AcquireUpgradeLockResponse(
        token=1
    )
    client.cluster.get_upgrade_state.return_value = None
    client.cluster.is_upgrade_active.return_value = False
    deployment.get_client.return_value = client
    return deployment


@pytest.fixture
def mock_deployment_with_hop():
    deployment = MagicMock()
    client = MagicMock()
    client.cluster.acquire_upgrade_lock.return_value = AcquireUpgradeLockResponse(
        token=1
    )
    client.cluster.get_upgrade_state.return_value = _state_json()
    client.cluster.is_upgrade_active.return_value = True
    client.cluster.update_upgrade_state.return_value = None
    client.cluster.release_upgrade_lock.return_value = None
    deployment.get_client.return_value = client
    return deployment


@pytest.fixture
def mock_deployment_lock_held():
    deployment = MagicMock()
    client = MagicMock()
    client.cluster.acquire_upgrade_lock.side_effect = UpgradeLockHeldException("held")
    client.cluster.get_upgrade_state.return_value = _state_json()
    deployment.get_client.return_value = client
    return deployment


class TestAbandonCommand:
    def test_abandon_with_yes_flag(self, mock_deployment_with_hop):
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["abandon", "--yes"], obj=mock_deployment_with_hop
        )
        assert result.exit_code == 0
        assert "abandoned" in result.output.lower()
        client = mock_deployment_with_hop.get_client.return_value
        client.cluster.update_upgrade_state.assert_called_once()

    def test_abandon_aborts_without_confirmation(self, mock_deployment_with_hop):
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["abandon"], input="n\n", obj=mock_deployment_with_hop
        )
        assert result.exit_code != 0

    def test_abandon_with_confirmation(self, mock_deployment_with_hop):
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["abandon"], input="y\n", obj=mock_deployment_with_hop
        )
        assert result.exit_code == 0
        assert "abandoned" in result.output.lower()

    def test_abandon_no_active_hop(self, mock_deployment_no_hop):
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["abandon", "--yes"], obj=mock_deployment_no_hop
        )
        assert result.exit_code != 0
        assert "no active upgrade hop" in result.output.lower()

    def test_abandon_prints_recovery_guidance(self, mock_deployment_with_hop):
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["abandon", "--yes"], obj=mock_deployment_with_hop
        )
        assert result.exit_code == 0
        assert "sunbeam restore" in result.output
        assert "sunbeam cluster upgrade preflight" in result.output

    def test_abandon_lock_held(self, mock_deployment_lock_held):
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["abandon", "--yes"], obj=mock_deployment_lock_held
        )
        assert result.exit_code != 0
        assert "lock" in result.output.lower()

    def test_abandon_releases_lock_after_success(self, mock_deployment_with_hop):
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["abandon", "--yes"], obj=mock_deployment_with_hop
        )
        assert result.exit_code == 0
        client = mock_deployment_with_hop.get_client.return_value
        client.cluster.release_upgrade_lock.assert_called_once_with(1)

    def test_abandon_shows_hop_details_in_prompt(self, mock_deployment_with_hop):
        runner = CliRunner()
        result = runner.invoke(
            upgrade, ["abandon"], input="n\n", obj=mock_deployment_with_hop
        )
        assert "2025.1" in result.output
        assert "2026.1" in result.output
