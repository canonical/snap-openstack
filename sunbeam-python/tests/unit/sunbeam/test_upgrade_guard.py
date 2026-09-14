# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for upgrade guard framework."""

from unittest.mock import MagicMock

import click
import pytest

from sunbeam.utils import (
    GUARDED_COMMANDS,
    GuardedGroup,
    check_upgrade_active,
)

# ---------------------------------------------------------------------------
# check_upgrade_active
# ---------------------------------------------------------------------------


class TestCheckUpgradeActive:
    def _make_deployment(self, is_active=False, client_error=False):
        deployment = MagicMock()
        client = MagicMock()
        client.cluster.is_upgrade_active.return_value = is_active
        if client_error:
            deployment.get_client.side_effect = ValueError("no client")
        else:
            deployment.get_client.return_value = client
        return deployment

    def test_raises_when_upgrade_active(self):
        deployment = self._make_deployment(is_active=True)
        with pytest.raises(click.ClickException) as exc_info:
            check_upgrade_active(deployment)
        assert "upgrade is in progress" in str(exc_info.value.message).lower()

    def test_passes_when_no_upgrade(self):
        deployment = self._make_deployment(is_active=False)
        check_upgrade_active(deployment)

    def test_passes_when_client_unavailable(self):
        deployment = self._make_deployment(client_error=True)
        check_upgrade_active(deployment)

    def test_passes_when_clusterd_unreachable(self):
        deployment = MagicMock()
        client = MagicMock()
        client.cluster.is_upgrade_active.side_effect = Exception("conn refused")
        deployment.get_client.return_value = client
        check_upgrade_active(deployment)


# ---------------------------------------------------------------------------
# GUARDED_COMMANDS
# ---------------------------------------------------------------------------


class TestGuardedCommands:
    def test_refresh_is_guarded(self):
        assert "refresh" in GUARDED_COMMANDS

    def test_bootstrap_is_guarded(self):
        assert "bootstrap" in GUARDED_COMMANDS

    def test_list_is_not_guarded(self):
        assert "list" not in GUARDED_COMMANDS

    def test_show_is_not_guarded(self):
        assert "show" not in GUARDED_COMMANDS

    def test_status_is_not_guarded(self):
        assert "status" not in GUARDED_COMMANDS


# ---------------------------------------------------------------------------
# GuardedGroup
# ---------------------------------------------------------------------------


class TestGuardedGroup:
    def test_is_subclass_of_catch_group(self):
        from sunbeam.utils import CatchGroup

        assert issubclass(GuardedGroup, CatchGroup)
