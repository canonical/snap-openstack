# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest
import tenacity
from watcherclient import v1 as watcher

from sunbeam.core.common import ResultType, SunbeamException
from sunbeam.core.juju import ActionFailedException, UnitNotFoundException
from sunbeam.steps.maintenance import (
    CordonControlRoleNodeStep,
    CreateWatcherAuditStepABC,
    CreateWatcherHostMaintenanceAuditStep,
    CreateWatcherWorkloadBalancingAuditStep,
    DrainControlRoleNodeStep,
    MicroCephActionStep,
    RunWatcherAuditStep,
    UncordonControlRoleNodeStep,
)
from sunbeam.steps.microceph import APPLICATION as _MICROCEPH_APPLICATION


@pytest.fixture(autouse=True)
def mock_watcher_helper():
    with patch("sunbeam.steps.maintenance.watcher_helper") as mock:
        yield mock


@pytest.fixture
def mock_watcher_client(mock_watcher_helper):
    mock_client = Mock()
    mock_watcher_helper.get_watcher_client.return_value = mock_client
    yield mock_client


class DummyCreateWatcherAuditStepABC(CreateWatcherAuditStepABC):
    name = "fake-name"
    description = "fake-desc"

    def __init__(self, mock_deployment, node):
        self.mock_audit = Mock()
        super().__init__(mock_deployment, node)

    def _create_audit(self) -> watcher.Audit:
        return self.mock_audit


class TestCreateWatcherAuditStepABC:
    def test_init(self, mock_watcher_helper):
        mock_deployment = Mock()
        DummyCreateWatcherAuditStepABC(mock_deployment, "fake-node")
        mock_watcher_helper.get_watcher_client.assert_called_once_with(
            deployment=mock_deployment
        )

    def test_get_actions(self, mock_watcher_helper, mock_watcher_client):
        step = DummyCreateWatcherAuditStepABC(Mock(), "fake-node")
        step._get_actions("fake-audit")
        mock_watcher_helper.get_actions.assert_called_once_with(
            client=mock_watcher_client, audit="fake-audit"
        )

    def test_run(self, mock_watcher_helper):
        step = DummyCreateWatcherAuditStepABC(Mock(), "fake-node")
        step._get_actions = Mock()
        result = step.run(Mock())
        assert result.result_type == ResultType.COMPLETED
        assert result.message == {
            "audit": step.mock_audit,
            "actions": step._get_actions.return_value,
        }

    def test_run_failed(self, mock_watcher_helper):
        step = DummyCreateWatcherAuditStepABC(Mock(), "fake-node")
        step._get_actions = Mock()
        step._get_actions.side_effect = tenacity.RetryError(Mock())
        result = step.run(Mock())

        assert result.result_type == ResultType.FAILED


class TestMicroCephActionStep:
    @patch("sunbeam.steps.maintenance.JujuActionHelper")
    def test_run(self, mock_action_helper):
        mock_client = Mock()
        mock_jhelper = Mock()

        step = MicroCephActionStep(
            mock_client,
            mock_jhelper,
            "fake-node",
            "fake-model",
            "fake-action-name",
            {"param1": "val1", "param2": "val2"},
        )

        result = step.run(Mock())
        mock_action_helper.run_action.assert_called_once_with(
            client=mock_client,
            jhelper=mock_jhelper,
            model="fake-model",
            node="fake-node",
            app=_MICROCEPH_APPLICATION,
            action_name="fake-action-name",
            action_params={"param1": "val1", "param2": "val2"},
        )
        assert result.result_type == ResultType.COMPLETED
        assert result.message == mock_action_helper.run_action.return_value

    @patch("sunbeam.steps.maintenance.JujuActionHelper")
    def test_run_unit_not_found_exception(self, mock_action_helper):
        mock_client = Mock()
        mock_jhelper = Mock()

        step = MicroCephActionStep(
            mock_client,
            mock_jhelper,
            "fake-node",
            "fake-model",
            "fake-action-name",
            {"param1": "val1", "param2": "val2"},
        )
        mock_action_helper.run_action.side_effect = UnitNotFoundException

        result = step.run(Mock())
        assert result.result_type == ResultType.FAILED

    @patch("sunbeam.steps.maintenance.JujuActionHelper")
    def test_run_action_failed_exception(self, mock_action_helper):
        mock_client = Mock()
        mock_jhelper = Mock()
        mock_action_result = Mock()

        step = MicroCephActionStep(
            mock_client,
            mock_jhelper,
            "fake-node",
            "fake-model",
            "fake-action-name",
            {"param1": "val1", "param2": "val2"},
        )
        mock_action_helper.run_action.side_effect = ActionFailedException(
            mock_action_result
        )

        result = step.run(Mock())
        assert result.result_type == ResultType.FAILED
        assert result.message == mock_action_result


class TestCreateWatcherHostMaintenanceAuditStep:
    def test_create_audit_default_migration_params(
        self, mock_watcher_helper, mock_watcher_client
    ):
        """Test _create_audit with default migration parameters (both False)"""
        step = CreateWatcherHostMaintenanceAuditStep(Mock(), "fake-node")
        result = step._create_audit()
        assert result == mock_watcher_helper.create_audit.return_value
        mock_watcher_helper.get_enable_maintenance_audit_template.assert_called_once_with(
            client=mock_watcher_client
        )
        mock_watcher_helper.create_audit.assert_called_once_with(
            client=mock_watcher_client,
            template=mock_watcher_helper.get_enable_maintenance_audit_template.return_value,
            parameters={
                "maintenance_node": "fake-node",
                "disable_live_migration": False,
                "disable_cold_migration": False,
            },
        )

    def test_create_audit_with_custom_migration_params(
        self, mock_watcher_helper, mock_watcher_client
    ):
        """Test _create_audit with custom migration parameters."""
        step = CreateWatcherHostMaintenanceAuditStep(
            Mock(),
            "fake-node",
            disable_live_migration=True,
            disable_cold_migration=False,
        )
        result = step._create_audit()
        assert result == mock_watcher_helper.create_audit.return_value
        mock_watcher_helper.get_enable_maintenance_audit_template.assert_called_once_with(
            client=mock_watcher_client
        )
        mock_watcher_helper.create_audit.assert_called_once_with(
            client=mock_watcher_client,
            template=mock_watcher_helper.get_enable_maintenance_audit_template.return_value,
            parameters={
                "maintenance_node": "fake-node",
                "disable_live_migration": True,
                "disable_cold_migration": False,
            },
        )


class TestCreateWatcherWorkloadBalancingAuditStep:
    def test_create_audit(self, mock_watcher_helper, mock_watcher_client):
        step = CreateWatcherWorkloadBalancingAuditStep(Mock(), "fake-node")
        result = step._create_audit()
        assert result == mock_watcher_helper.create_audit.return_value
        mock_watcher_helper.get_workload_balancing_audit_template.assert_called_once_with(
            client=mock_watcher_client
        )
        mock_watcher_helper.create_audit.assert_called_once_with(
            client=mock_watcher_client,
            template=mock_watcher_helper.get_workload_balancing_audit_template.return_value,
        )


class TestRunWatcherAuditStep:
    def test_run(self, mock_watcher_helper, mock_watcher_client):
        mock_audit = Mock()
        mock_status = Mock()
        step = RunWatcherAuditStep(Mock(), "fake-node", mock_audit)

        result = step.run(mock_status)
        assert result.result_type == ResultType.COMPLETED
        assert result.message == mock_watcher_helper.get_actions.return_value

        mock_watcher_helper.exec_audit.assert_called_once_with(
            mock_watcher_client, mock_audit
        )
        mock_watcher_helper.wait_until_action_state.assert_called_once_with(
            step=step,
            audit=mock_audit,
            client=mock_watcher_client,
            status=mock_status,
        )
        mock_watcher_helper.get_actions.assert_called_once_with(
            client=mock_watcher_client, audit=mock_audit
        )

    def test_run_failed(self, mock_watcher_helper, mock_watcher_client):
        mock_audit = Mock()
        mock_status = Mock()
        mock_deployment = Mock()

        # Mock helper methods
        mock_watcher_helper.exec_audit.side_effect = SunbeamException(
            "Execution failed"
        )
        mock_watcher_helper.get_actions.return_value = "mock-actions"

        step = RunWatcherAuditStep(mock_deployment, "fake-node", mock_audit)

        # Call the method under test
        result = step.run(mock_status)

        # Assertions
        assert result.result_type == ResultType.FAILED
        assert result.message == "mock-actions"

        mock_watcher_helper.exec_audit.assert_called_once_with(
            mock_watcher_client, mock_audit
        )
        mock_watcher_helper.wait_until_action_state.assert_not_called()
        mock_watcher_helper.get_actions.assert_called_once_with(
            client=mock_watcher_client, audit=mock_audit
        )


class TestDrainControlRoleNodeStep:
    def test_run(self):
        with patch("sunbeam.steps.maintenance.DrainK8SUnitStep.run") as parent_run:
            step = DrainControlRoleNodeStep(
                "fake-node", Mock(), Mock(), "fake-model", dry_run=False
            )
            step.run(Mock())
            parent_run.assert_called_once()

    def test_dry_run(self):
        with patch("sunbeam.steps.maintenance.DrainK8SUnitStep.run") as parent_run:
            step = DrainControlRoleNodeStep(
                "fake-node", Mock(), Mock(), "fake-model", dry_run=True
            )
            step.run(Mock())
            parent_run.assert_not_called()


class TestCordonControlRoleNodeStep:
    def test_run(self):
        with patch("sunbeam.steps.maintenance.CordonK8SUnitStep.run") as parent_run:
            step = CordonControlRoleNodeStep(
                "fake-node", Mock(), Mock(), "fake-model", dry_run=False
            )
            step.run(Mock())
            parent_run.assert_called_once()

    def test_dry_run(self):
        with patch("sunbeam.steps.maintenance.CordonK8SUnitStep.run") as parent_run:
            step = CordonControlRoleNodeStep(
                "fake-node", Mock(), Mock(), "fake-model", dry_run=True
            )
            step.run(Mock())
            parent_run.assert_not_called()


class TestUncordonControlRoleNodeStep:
    def test_run(self):
        with patch("sunbeam.steps.maintenance.UncordonK8SUnitStep.run") as parent_run:
            step = UncordonControlRoleNodeStep(
                "fake-node", Mock(), Mock(), "fake-model", dry_run=False
            )
            step.run(Mock())
            parent_run.assert_called_once()

    def test_dry_run(self):
        with patch("sunbeam.steps.maintenance.UncordonK8SUnitStep.run") as parent_run:
            step = UncordonControlRoleNodeStep(
                "fake-node", Mock(), Mock(), "fake-model", dry_run=True
            )
            step.run(Mock())
            parent_run.assert_not_called()
