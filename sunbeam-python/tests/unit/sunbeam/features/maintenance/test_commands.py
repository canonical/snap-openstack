# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.features.maintenance.commands import EnableMaintenance, enable


class TestDisableMigrationFlagMapping:
    """Test the --disable-migration CLI flag to boolean mapping logic.

    This tests the mapping in the enable() Click command that converts
    --disable-migration choice values to disable_live/cold_migration booleans.
    """

    @pytest.mark.parametrize(
        "disable_migration,expected_live,expected_cold",
        [
            (None, False, False),  # Not specified
            ("both", True, True),  # --disable-migration (flag_value default)
            ("live", True, False),  # --disable-migration live
            ("cold", False, True),  # --disable-migration cold
        ],
    )
    def test_disable_migration_flag_mapping(
        self,
        disable_migration,
        expected_live,
        expected_cold,
    ):
        """Test --disable-migration maps to correct boolean params."""
        with (
            patch(
                "sunbeam.features.maintenance.commands.EnableMaintenance"
            ) as mock_cls,
            patch(
                "sunbeam.features.maintenance.commands.get_cluster_status"
            ) as mock_get_status,
            patch("sunbeam.features.maintenance.commands.JujuHelper"),
        ):
            mock_get_status.return_value = {"test-node": "compute"}
            mock_instance = Mock()
            mock_cls.return_value = mock_instance

            # enable is wrapped by pass_method_obj which calls
            # click.get_current_context() to inject deployment.
            mock_ctx = Mock()
            mock_ctx.obj = Mock()  # deployment injected by pass_method_obj
            with patch("click.get_current_context", return_value=mock_ctx):
                enable.callback(
                    None,  # self (from pass_method_obj)
                    node="test-node",
                    force=False,
                    dry_run=True,
                    enable_ceph_crush_rebalancing=False,
                    stop_osds=False,
                    allow_downtime=False,
                    disable_migration=disable_migration,
                    show_hints=False,
                )

            # Verify EnableMaintenance was constructed with correct booleans
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["disable_live_migration"] == expected_live
            assert call_kwargs["disable_cold_migration"] == expected_cold


class TestEnableMaintenance:
    """Test cases for the EnableMaintenance class."""

    @pytest.fixture
    def mock_deployment(self):
        """Mock deployment object."""
        deployment = Mock()
        deployment.openstack_machines_model = "test-model"
        deployment.get_client.return_value = Mock()
        deployment.juju_controller = "test-controller"
        return deployment

    @pytest.fixture
    def cluster_status(self):
        """Mock cluster status."""
        return {"test-node": "compute"}

    @pytest.mark.parametrize(
        "disable_live_migration,disable_cold_migration",
        [
            (False, False),  # Default case
            (True, True),  # Both disabled
            (True, False),  # Only live disabled
            (False, True),  # Only cold disabled
        ],
    )
    def test_dry_run_creates_watcher_step_with_correct_migration_params(
        self,
        mock_deployment,
        cluster_status,
        disable_live_migration,
        disable_cold_migration,
    ):
        """Test dry_run creates CreateWatcherHostMaintenanceAuditStep correctly."""
        with (
            patch(
                "sunbeam.features.maintenance.commands.CreateWatcherHostMaintenanceAuditStep"
            ) as mock_create_watcher_step,
            patch("sunbeam.features.maintenance.commands.run_plan") as mock_run_plan,
            patch("sunbeam.features.maintenance.commands.JujuHelper"),
            patch("sunbeam.features.maintenance.commands.OperationViewer"),
        ):
            # Set up mock class name for get_step_message
            mock_create_watcher_step.__name__ = "CreateWatcherHostMaintenanceAuditStep"

            # Mock run_plan return value
            mock_result = Mock()
            mock_result.message = {"actions": []}
            mock_run_plan.return_value = {
                "CreateWatcherHostMaintenanceAuditStep": mock_result
            }

            # Create EnableMaintenance instance
            enable_maintenance = EnableMaintenance(
                node="test-node",
                deployment=mock_deployment,
                cluster_status=cluster_status,
                disable_live_migration=disable_live_migration,
                disable_cold_migration=disable_cold_migration,
            )

            # Mock console for dry_run
            mock_console = Mock()

            # Call dry_run
            enable_maintenance.dry_run(mock_console, show_hints=False)

            # Verify CreateWatcherHostMaintenanceAuditStep called with correct params
            mock_create_watcher_step.assert_called_once_with(
                deployment=mock_deployment,
                node="test-node",
                disable_live_migration=disable_live_migration,
                disable_cold_migration=disable_cold_migration,
            )
