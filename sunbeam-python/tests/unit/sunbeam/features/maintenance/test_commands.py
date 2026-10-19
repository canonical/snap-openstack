# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.features.maintenance.commands import EnableMaintenance, enable
from sunbeam.features.maintenance.resolve import ResolvedNode


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
            patch("sunbeam.features.maintenance.commands.run_preflight_checks"),
            patch("sunbeam.features.maintenance.commands.run_plan"),
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

    @pytest.fixture
    def resolved(self, mock_deployment, cluster_status):
        """Resolved node for the mocked deployment."""
        return ResolvedNode("test-node", mock_deployment, cluster_status, Mock())

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
        resolved,
        disable_live_migration,
        disable_cold_migration,
    ):
        """Test dry_run creates CreateWatcherHostMaintenanceAuditStep correctly."""
        hypervisor = Mock(hypervisor_hostname="test-node")
        with (
            patch(
                "sunbeam.features.maintenance.commands.CreateWatcherHostMaintenanceAuditStep"
            ) as mock_create_watcher_step,
            patch("sunbeam.features.maintenance.commands.run_plan") as mock_run_plan,
            patch("sunbeam.features.maintenance.commands.JujuHelper"),
            patch("sunbeam.features.maintenance.commands.OperationViewer"),
            patch(
                "sunbeam.features.maintenance.resolve.get_admin_connection"
            ) as mock_admin_conn,
        ):
            mock_admin_conn.return_value.compute.hypervisors.return_value = [hypervisor]
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
                resolved=resolved,
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

    def test_dry_run_control_role_steps_use_clusterd_name(self, mock_deployment):
        """Cordon/drain steps are keyed on the clusterd node name (LP#2129793).

        The steps' is_skip looks the node up in clusterd by exact name, so
        they must receive the clusterd name even when the k8s node name
        differs (short hostname vs FQDN).
        """
        cluster_status = {"test-node": "control"}
        jhelper = Mock()
        machine = Mock(hostname="test-node", dns_name="10.0.0.1")
        jhelper.get_machines.return_value = {"0": machine}
        mock_deployment.get_client.return_value.cluster.list_nodes.return_value = [
            {"name": "test-node.example.com", "machineid": 0}
        ]
        resolved = ResolvedNode("test-node", mock_deployment, cluster_status, jhelper)

        result = Mock()
        result.message = {"id": "Cordon 'test-node.example.com'"}
        with (
            patch(
                "sunbeam.features.maintenance.commands.CordonControlRoleNodeStep"
            ) as mock_cordon_step,
            patch(
                "sunbeam.features.maintenance.commands.DrainControlRoleNodeStep"
            ) as mock_drain_step,
            patch("sunbeam.features.maintenance.commands.run_plan") as mock_run_plan,
            patch("sunbeam.features.maintenance.commands.JujuHelper"),
            patch("sunbeam.features.maintenance.commands.OperationViewer"),
        ):
            mock_cordon_step.__name__ = "CordonControlRoleNodeStep"
            mock_drain_step.__name__ = "DrainControlRoleNodeStep"
            mock_run_plan.return_value = {
                "CordonControlRoleNodeStep": result,
                "DrainControlRoleNodeStep": result,
            }

            enable_maintenance = EnableMaintenance(
                resolved=resolved,
                deployment=mock_deployment,
                cluster_status=cluster_status,
            )
            enable_maintenance.dry_run(Mock(), show_hints=False)

        mock_cordon_step.assert_called_once()
        assert mock_cordon_step.call_args[0][0] == "test-node.example.com"
        mock_drain_step.assert_called_once()
        assert mock_drain_step.call_args[0][0] == "test-node.example.com"
