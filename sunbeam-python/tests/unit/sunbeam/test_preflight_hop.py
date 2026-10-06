# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for active hop creation after preflight."""

import json
from unittest.mock import MagicMock, patch

import pytest

from sunbeam.clusterd.models import AcquireUpgradeLockResponse
from sunbeam.clusterd.service import UpgradeLockHeldException
from sunbeam.upgrades.metadata import HopMetadata
from sunbeam.upgrades.preflight.hop import (
    UPGRADE_METADATA_KEY,
    create_hop_after_preflight,
)
from sunbeam.upgrades.state import HopStatus

FROM = "2025.1"
TO = "2026.1"


def _make_metadata(from_release: str = FROM, to_release: str = TO) -> HopMetadata:
    return HopMetadata.model_validate(
        {
            "from": from_release,
            "to": to_release,
            "control_plane_groups": [
                {"name": "identity-core", "apps": ["keystone-k8s"]}
            ],
        }
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
    client.cluster.update_config.return_value = None
    return client


class TestCreateHopAfterPreflight:
    def test_creates_hop_with_pending_status(self, mock_client):
        with patch(
            "sunbeam.upgrades.preflight.hop._get_snap_revision",
            return_value="123",
        ):
            hop = create_hop_after_preflight(mock_client, FROM, TO, _make_metadata())
        assert hop.from_release == FROM
        assert hop.to_release == TO
        assert hop.status == HopStatus.PENDING
        assert hop.metadata_build_id == "123"

    def test_writes_metadata_to_clusterd(self, mock_client):
        metadata = _make_metadata()
        with patch(
            "sunbeam.upgrades.preflight.hop._get_snap_revision",
            return_value="123",
        ):
            create_hop_after_preflight(mock_client, FROM, TO, metadata)
        mock_client.cluster.update_config.assert_called_once()
        args = mock_client.cluster.update_config.call_args
        assert args[0][0] == UPGRADE_METADATA_KEY
        assert args[0][1]["from"] == FROM
        assert args[0][1]["to"] == TO

    def test_persists_state(self, mock_client):
        with patch(
            "sunbeam.upgrades.preflight.hop._get_snap_revision",
            return_value="123",
        ):
            create_hop_after_preflight(mock_client, FROM, TO, _make_metadata())
        mock_client.cluster.update_upgrade_state.assert_called()

    def test_releases_lock_after_success(self, mock_client):
        with patch(
            "sunbeam.upgrades.preflight.hop._get_snap_revision",
            return_value="123",
        ):
            create_hop_after_preflight(mock_client, FROM, TO, _make_metadata())
        mock_client.cluster.release_upgrade_lock.assert_called_once_with(1)

    def test_releases_lock_on_failure(self, mock_client):
        mock_client.cluster.update_config.side_effect = Exception("config write failed")
        with patch(
            "sunbeam.upgrades.preflight.hop._get_snap_revision",
            return_value="123",
        ):
            with pytest.raises(Exception):
                create_hop_after_preflight(mock_client, FROM, TO, _make_metadata())
        mock_client.cluster.release_upgrade_lock.assert_called_once_with(1)

    def test_raises_if_lock_held(self, mock_client):
        mock_client.cluster.acquire_upgrade_lock.side_effect = UpgradeLockHeldException(
            "held"
        )
        with pytest.raises(UpgradeLockHeldException):
            create_hop_after_preflight(mock_client, FROM, TO, _make_metadata())

    def test_raises_if_active_hop_already_exists(self, mock_client):
        state = {
            "active_hop": {"hop_history_index": 0},
            "hop_history": [
                {
                    "from": FROM,
                    "to": TO,
                    "metadata_version": 1,
                    "metadata_build_id": "999",
                    "status": "in_progress",
                }
            ],
        }
        mock_client.cluster.get_upgrade_state.return_value = json.dumps(state)
        with patch(
            "sunbeam.upgrades.preflight.hop._get_snap_revision",
            return_value="123",
        ):
            with pytest.raises(RuntimeError, match="already exists"):
                create_hop_after_preflight(mock_client, FROM, TO, _make_metadata())

    def test_sets_metadata_build_id_from_snap_revision(self, mock_client):
        with patch(
            "sunbeam.upgrades.preflight.hop._get_snap_revision",
            return_value="456",
        ):
            hop = create_hop_after_preflight(mock_client, FROM, TO, _make_metadata())
        assert hop.metadata_build_id == "456"
