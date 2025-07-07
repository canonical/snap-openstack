# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0
from os import linesep
from unittest.mock import Mock, PropertyMock, call, patch

import click
import pytest

from sunbeam.core.common import ResultType
from sunbeam.features.maintenance.utils import (
    OperationGoal,
    OperationViewer,
    get_cluster_status,
)
from sunbeam.steps.hypervisor import EnableHypervisorStep
from sunbeam.steps.maintenance import (
    MicroCephActionStep,
    RunWatcherAuditStep,
)


@patch("sunbeam.features.maintenance.utils.LocalClusterStatusStep")
@patch("sunbeam.features.maintenance.utils.run_plan")
@patch("sunbeam.features.maintenance.utils.get_step_message")
def test_get_cluster_status(
    mock_get_step_message,
    mock_run_plan,
    mock_local_cluster_status_step,
):
    mock_deployment = Mock()
    mock_deployment.type = "local"
    mock_deployment.openstack_machines_model = "openstack-machines"
    mock_jhelper = Mock()
    mock_get_step_message.return_value = {
        "openstack-machines": {
            "key-a": {
                "status": "target-status-val",
                "hostname": "fake-node-a",
            },
            "key-b": {
                "status": "not-target-status-val",
                "hostname": "fake-node-b",
            },
            "key-c": {
                "status": "not-target-status-val",
                "hostname": "fake-node-c",
            },
        }
    }

    result = get_cluster_status(mock_deployment, mock_jhelper, "console", False)

    mock_local_cluster_status_step.assert_called_once_with(
        mock_deployment, mock_jhelper
    )
    mock_run_plan.assert_called_once_with(
        [mock_local_cluster_status_step.return_value], "console", False
    )
    mock_get_step_message.assert_called_once_with(
        mock_run_plan.return_value, mock_local_cluster_status_step
    )
    assert result == {
        "fake-node-a": "target-status-val",
        "fake-node-b": "not-target-status-val",
        "fake-node-c": "not-target-status-val",
    }


class TestOperationViewer:
    def test_operation_plan(self):
        viewer = OperationViewer("fake-node")
        viewer.operations = ["op0", "op1", "op2", "op3"]
        assert viewer._operation_plan == (
            f"\t0: op0{linesep}\t1: op1{linesep}\t2: op2{linesep}\t3: op3{linesep}"
        )

    def test_operation_result(self):
        viewer = OperationViewer("fake-node")
        viewer.operations = ["op0", "op1", "op2", "op3"]
        viewer.operation_states = {
            "op0": "result0",
            "op1": "result1",
            "op2": "result2",
            "op3": "result3",
        }
        assert viewer._operation_result == (
            f"Operation result:{linesep}"
            f"\t0: op0 result0{linesep}"
            f"\t1: op1 result1{linesep}"
            f"\t2: op2 result2{linesep}"
            f"\t3: op3 result3{linesep}"
        )

    def test_operation_result_empty_operations(self):
        viewer = OperationViewer("fake-node")
        assert viewer._operation_result == ""

    @pytest.mark.parametrize(
        "goal,expected",
        [
            (
                OperationGoal.EnableMaintenance,
                (
                    "Required operations to enable maintenance mode for fake-node:"
                    f"{linesep}fake-operation-plan"
                ),
            ),
            (
                OperationGoal.DisableMaintenance,
                (
                    "Required operations to disable maintenance mode for fake-node:"
                    f"{linesep}fake-operation-plan"
                ),
            ),
        ],
    )
    def test_dry_run_message(self, goal, expected):
        with patch.object(
            OperationViewer, "_operation_plan", new_callable=PropertyMock
        ) as mock_property:
            mock_property.return_value = "fake-operation-plan"
            viewer = OperationViewer("fake-node", goal)
            assert viewer.dry_run_message == expected

    def test_get_watcher_action_key_change_nova_service_state(self):
        mock_action = Mock()
        mock_action.action_type = "change_nova_service_state"
        mock_action.input_parameters = {
            "state": "fake-state",
            "resource_name": "fake-resource",
        }
        key = OperationViewer._get_watcher_action_key(mock_action)
        assert (
            key == "change_nova_service_state state=fake-state resource=fake-resource"
        )

    def test_get_watcher_action_key_migrate(self):
        mock_action = Mock()
        mock_action.action_type = "migrate"
        mock_action.input_parameters = {
            "migration_type": "fake-type",
            "resource_name": "fake-resource",
        }
        key = OperationViewer._get_watcher_action_key(mock_action)
        assert key == "Migrate instance type=fake-type resource=fake-resource"

    def test_add_watcher_actions(self):
        actions = ["action1", "action2", "action3"]
        viewer = OperationViewer("fake-node")
        viewer._get_watcher_action_key = Mock()
        viewer._get_watcher_action_key.side_effect = ["key1", "key2", "key3"]

        viewer.add_watch_actions(actions)
        assert viewer.operations == ["key1", "key2", "key3"]
        assert viewer.operation_states == {
            "key1": "PENDING",
            "key2": "PENDING",
            "key3": "PENDING",
        }
        viewer._get_watcher_action_key.assert_has_calls(
            [
                call("action1"),
                call("action2"),
                call("action3"),
            ]
        )

    def test_add_maintenance_action_steps(self):
        viewer = OperationViewer("fake-node")
        action_result = {
            "actions": {
                "step1": {"id": "id-1"},
                "step2": {"id": "id-2"},
                "step3": {"id": "id-3"},
            }
        }
        viewer.add_maintenance_action_steps(action_result)

        assert viewer.operations == ["id-1", "id-2", "id-3"]
        assert viewer.operation_states == {
            "id-1": "SKIPPED",
            "id-2": "SKIPPED",
            "id-3": "SKIPPED",
        }

    def test_add_step(self):
        viewer = OperationViewer("fake-node")
        steps = ["step1", "step2", "step3"]
        for step in steps:
            viewer.add_step(step)

        assert viewer.operations == ["step1", "step2", "step3"]
        assert viewer.operation_states == {
            "step1": "SKIPPED",
            "step2": "SKIPPED",
            "step3": "SKIPPED",
        }

    def test_update_watcher_actions_result(self):
        viewer = OperationViewer("fake-node")
        viewer._get_watcher_action_key = Mock()
        viewer._get_watcher_action_key.side_effect = ["key1", "key2", "key3"]
        viewer.operation_states = {
            "key1": "non-updated",
            "key2": "non-updated",
            "key3": "non-updated",
        }
        actions = [Mock(), Mock(), Mock()]
        for action in actions:
            action.state = "SUCCEEDED"
        viewer.update_watcher_actions_result(actions)

        viewer._get_watcher_action_key.assert_has_calls(
            [
                call(actions[0]),
                call(actions[1]),
                call(actions[2]),
            ]
        )
        assert viewer.operation_states == {
            "key1": "SUCCEEDED",
            "key2": "SUCCEEDED",
            "key3": "SUCCEEDED",
        }

    def test_update_maintenance_action_steps_result(self):
        viewer = OperationViewer("fake-node")
        viewer.operation_states = {
            "id-1": "SKIPPED",
            "id-2": "SKIPPED",
            "id-3": "SKIPPED",
        }

        action_result = {
            "actions": {
                "step1": {"id": "id-1"},
                "step2": {"id": "id-2", "error": "some-error-msg"},
            }
        }
        viewer.update_maintenance_action_steps_result(action_result)
        assert viewer.operation_states == {
            "id-1": "SUCCEEDED",
            "id-2": "FAILED",
            "id-3": "SKIPPED",
        }

    def test_update_step_result_completed(self):
        viewer = OperationViewer("fake-node")
        viewer.operation_states = {
            "step1": "SKIPPED",
            "step2": "SKIPPED",
            "step3": "SKIPPED",
        }
        result = Mock()
        result.result_type = ResultType.COMPLETED
        viewer.update_step_result("step2", result)
        assert viewer.operation_states == {
            "step1": "SKIPPED",
            "step2": "SUCCEEDED",
            "step3": "SKIPPED",
        }

    def test_update_step_result_failed(self):
        viewer = OperationViewer("fake-node")
        viewer.operation_states = {
            "step1": "SKIPPED",
            "step2": "SKIPPED",
            "step3": "SKIPPED",
        }
        result = Mock()
        result.result_type = ResultType.FAILED
        viewer.update_step_result("step2", result)
        assert viewer.operation_states == {
            "step1": "SKIPPED",
            "step2": "FAILED",
            "step3": "SKIPPED",
        }

    def test_update_step_result_skipped(self):
        viewer = OperationViewer("fake-node")
        viewer.operation_states = {
            "step1": "SKIPPED",
            "step2": "SKIPPED",
            "step3": "SKIPPED",
        }
        result = Mock()
        result.result_type = ResultType.SKIPPED
        viewer.update_step_result("step2", result)
        assert viewer.operation_states == {
            "step1": "SKIPPED",
            "step2": "SKIPPED",
            "step3": "SKIPPED",
        }

    @pytest.mark.parametrize(
        "goal,expected",
        [
            (
                OperationGoal.EnableMaintenance,
                (
                    f"Continue to run operations to enable maintenance mode for fake-node: {linesep}"
                ),
            ),
            (
                OperationGoal.DisableMaintenance,
                (
                    f"Continue to run operations to disable maintenance mode for fake-node: {linesep}"
                ),
            ),
        ],
    )
    @patch("sunbeam.features.maintenance.utils.ConfirmQuestion")
    def test_prompt(self, mock_confirm_question, goal, expected):
        viewer = OperationViewer("fake-node", goal)
        mock_confirm_question.return_value = Mock()

        with patch.object(
            OperationViewer, "_operation_plan", new_callable=PropertyMock
        ) as mock_property:
            viewer.prompt()

            mock_confirm_question.assert_called_once_with(
                expected + mock_property.return_value
            )
            mock_confirm_question.return_value.ask.assert_called_once()

    @patch("sunbeam.features.maintenance.utils.console")
    def test_check_operation_succeeded(self, mock_console):
        viewer = OperationViewer("fake-node")
        viewer.update_watcher_actions_result = Mock()
        viewer.update_maintenance_action_steps_result = Mock()
        viewer.update_step_result = Mock()

        results = {
            RunWatcherAuditStep.__name__: Mock(),
            MicroCephActionStep.__name__: Mock(),
            EnableHypervisorStep.__name__: Mock(),
        }

        with patch.object(
            OperationViewer, "_operation_result", new_callable=PropertyMock
        ) as mock_property:
            viewer.check_operation_succeeded(results)
            viewer.update_watcher_actions_result.assert_called_once_with(
                results[RunWatcherAuditStep.__name__].message
            )
            viewer.update_maintenance_action_steps_result.assert_called_once_with(
                results[MicroCephActionStep.__name__].message
            )
            viewer.update_step_result.assert_called_once_with(
                EnableHypervisorStep.__name__,
                results[EnableHypervisorStep.__name__],
            )
            mock_console.print.assert_called_once_with(mock_property.return_value)

    @patch("sunbeam.features.maintenance.utils.console")
    def test_check_operation_succeeded_failed(self, mock_console):
        viewer = OperationViewer("fake-node")
        viewer._raise_exception = Mock()
        viewer.update_watcher_actions_result = Mock()
        viewer.update_maintenance_action_steps_result = Mock()
        viewer.update_step_result = Mock()

        results = {
            RunWatcherAuditStep.__name__: Mock(),
            MicroCephActionStep.__name__: Mock(),
            EnableHypervisorStep.__name__: Mock(),
        }
        results[RunWatcherAuditStep.__name__].result_type = ResultType.FAILED

        with patch.object(
            OperationViewer, "_operation_result", new_callable=PropertyMock
        ) as mock_property:
            viewer.check_operation_succeeded(results)
            mock_console.print.assert_called_once_with(mock_property.return_value)
            viewer._raise_exception.assert_called_once_with(
                RunWatcherAuditStep.__name__,
                results[RunWatcherAuditStep.__name__],
            )

    def test_raise_expection(self):
        viewer = OperationViewer("fake-node")
        result = Mock()
        result.message = "fake-err-msg"
        with pytest.raises(click.ClickException, match="fake-err-msg"):
            viewer._raise_exception("fake-name", result)

    def test_raise_expection_microceph_action(self):
        viewer = OperationViewer("fake-node")
        result = Mock()
        result.message = {"errors": "fake-err-msg"}
        with pytest.raises(click.ClickException, match="fake-err-msg"):
            viewer._raise_exception(MicroCephActionStep.__name__, result)
