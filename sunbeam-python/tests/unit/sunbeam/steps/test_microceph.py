# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

from sunbeam.core.common import ResultType
from sunbeam.core.juju import ActionFailedException
from sunbeam.features.microceph.steps import (
    ConfigureMicrocephOSDStep,
    SetCephMgrPoolSizeStep,
)


class TestConfigureMicrocephOSDStep:
    def test_is_skip(self, cclient, jhelper):
        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run(self, cclient, jhelper):
        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        step.wipe = False
        result = step.run()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_action_failed(self, cclient, jhelper):
        jhelper.run_action.side_effect = ActionFailedException("Action failed...")

        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        result = step.run()

        jhelper.run_action.assert_called_once()
        expected_message = (
            f"Microceph Adding disks {step.disks} failed: Action failed..."
        )
        assert result.result_type == ResultType.FAILED
        assert result.message == expected_message

    def test_run_with_already_added_disks(self, cclient, jhelper):
        error_msg = (
            "[{'spec': '/dev/sdb', 'status': 'failure', 'message': 'Error: failed"
            'to record disk: This "disks" entry already exists\\n\'}]'
        )
        error_result = {"result": error_msg, "return-code": 0}
        jhelper.run_action.side_effect = ActionFailedException(error_result)

        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb"
        step.wipe = False
        result = step.run()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_wipe_true(self, cclient, jhelper):
        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        step.wipe = True
        jhelper.get_unit_from_machine = Mock(return_value="unit/0")
        jhelper.run_action = Mock(return_value={"status": "completed"})
        result = step.run()

        jhelper.run_action.assert_called_once_with(
            "unit/0",
            "test-model",
            "add-osd",
            action_params={"device-id": "/dev/sdb,/dev/sdc", "wipe": True},
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_wipe_false(self, cclient, jhelper):
        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        step.wipe = False
        jhelper.get_unit_from_machine = Mock(return_value="unit/0")
        jhelper.run_action = Mock(return_value={"status": "completed"})
        result = step.run()

        jhelper.run_action.assert_called_once_with(
            "unit/0",
            "test-model",
            "add-osd",
            action_params={"device-id": "/dev/sdb,/dev/sdc"},
        )
        assert result.result_type == ResultType.COMPLETED


class TestSetCephMgrPoolSizeStep:
    def test_is_skip(self, cclient, jhelper):
        cclient.cluster.list_nodes_by_role.return_value = []
        step = SetCephMgrPoolSizeStep(cclient, jhelper, "test-model")
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_storage_nodes(self, cclient, jhelper):
        cclient.cluster.list_nodes_by_role.return_value = ["sunbeam1"]
        step = SetCephMgrPoolSizeStep(cclient, jhelper, "test-model")
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run(self, cclient, jhelper):
        jhelper.run_action.return_value = Mock()
        step = SetCephMgrPoolSizeStep(cclient, jhelper, "test-model")
        result = step.run()

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_action_failed(self, cclient, jhelper):
        jhelper.run_action.side_effect = ActionFailedException("Action failed...")

        step = SetCephMgrPoolSizeStep(cclient, jhelper, "test-model")
        result = step.run()

        jhelper.run_action.assert_called_once()
        expected_message = "Action failed..."
        assert result.result_type == ResultType.FAILED
        assert result.message == expected_message
