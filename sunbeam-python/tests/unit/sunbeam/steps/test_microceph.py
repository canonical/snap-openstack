# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ActionFailedException,
    ExecFailedException,
    UnitNotFoundException,
)
from sunbeam.steps.microceph import (
    ConfigureMicrocephOSDStep,
    RemoveMicrocephOSDsStep,
    SetCephMgrPoolSizeStep,
)


def _command_result(stdout=""):
    return Mock(stdout=stdout)


def _configured_disks(disks):
    return json.dumps({"ConfiguredDisks": disks})


def _crush_tree(hostname=None, children=None):
    nodes = []
    if hostname is not None:
        nodes.append({"name": hostname, "type": "host", "children": children or []})
    return json.dumps({"nodes": nodes})


def _cleanup_step(cclient, jhelper, force=False):
    cclient.cluster.get_node_info.return_value = {"machineid": "1"}
    jhelper.get_unit_from_machine.return_value = "microceph/0"
    return RemoveMicrocephOSDsStep(
        cclient,
        "node-1",
        jhelper,
        "test-model",
        hostnames=("node-1", "node-1.maas"),
        force=force,
    )


class TestConfigureMicrocephOSDStep:
    def test_is_skip(self, cclient, jhelper, step_context):
        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_run(self, cclient, jhelper, step_context):
        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        step.wipe = False
        result = step.run(step_context)

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_action_failed(self, cclient, jhelper, step_context):
        jhelper.run_action.side_effect = ActionFailedException("Action failed...")

        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        result = step.run(step_context)

        jhelper.run_action.assert_called_once()
        expected_message = (
            f"Microceph Adding disks {step.disks} failed: Action failed..."
        )
        assert result.result_type == ResultType.FAILED
        assert result.message == expected_message

    def test_run_with_already_added_disks(self, cclient, jhelper, step_context):
        error_msg = (
            "[{'spec': '/dev/sdb', 'status': 'failure', 'message': 'Error: failed"
            'to record disk: This "disks" entry already exists\\n\'}]'
        )
        error_result = {"result": error_msg, "return-code": 0}
        jhelper.run_action.side_effect = ActionFailedException(error_result)

        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb"
        step.wipe = False
        result = step.run(step_context)

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_wipe_true(self, cclient, jhelper, step_context):
        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        step.wipe = True
        jhelper.get_unit_from_machine = Mock(return_value="unit/0")
        jhelper.run_action = Mock(return_value={"status": "completed"})
        result = step.run(step_context)

        jhelper.run_action.assert_called_once_with(
            "unit/0",
            "test-model",
            "add-osd",
            action_params={"device-id": "/dev/sdb,/dev/sdc", "wipe": True},
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_wipe_false(self, cclient, jhelper, step_context):
        step = ConfigureMicrocephOSDStep(cclient, "test-0", jhelper, "test-model")
        step.disks = "/dev/sdb,/dev/sdc"
        step.wipe = False
        jhelper.get_unit_from_machine = Mock(return_value="unit/0")
        jhelper.run_action = Mock(return_value={"status": "completed"})
        result = step.run(step_context)

        jhelper.run_action.assert_called_once_with(
            "unit/0",
            "test-model",
            "add-osd",
            action_params={"device-id": "/dev/sdb,/dev/sdc"},
        )
        assert result.result_type == ResultType.COMPLETED


class TestRemoveMicrocephOSDsStep:
    def test_removes_host_osds_and_verifies_state(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                _configured_disks(
                    [
                        {"osd": 5, "location": "node-1.maas"},
                        {"osd": 2, "location": "node-1"},
                        {"osd": 9, "location": "node-2"},
                    ]
                )
            ),
            _command_result(_crush_tree("node-1.maas", [5])),
            _command_result(),
            _command_result(),
            _command_result(_configured_disks([{"osd": 9, "location": "node-2"}])),
            _command_result(_crush_tree()),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.unit == "microceph/0"
        jhelper.get_unit_from_machine.assert_called_once_with(
            "microceph", "1", "test-model"
        )
        jhelper.get_leader_unit.assert_not_called()
        assert [
            call.args[2]
            for call in jhelper.run_cmd_on_machine_unit_payload.call_args_list
        ] == [
            "microceph disk list --json",
            "microceph.ceph osd tree --format json",
            "microceph disk remove osd.2 --timeout 1800",
            "microceph disk remove osd.5 --timeout 1800",
            "microceph disk list --json",
            "microceph.ceph osd tree --format json",
        ]

    def test_crush_only_osd_aborts_before_removal(self, cclient, jhelper, step_context):
        jhelper.get_unit_from_machine.side_effect = UnitNotFoundException(
            "unit is missing"
        )
        jhelper.get_leader_unit.return_value = "microceph/3"
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(_configured_disks([{"osd": 2, "location": "node-1"}])),
            _command_result(_crush_tree("node-1", [2, 7])),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert step.unit == "microceph/3"
        assert jhelper.run_cmd_on_machine_unit_payload.call_count == 2

    def test_force_does_not_bypass_safety_checks(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper, force=True)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(_configured_disks([{"osd": 2, "location": "node-1"}])),
            _command_result(_crush_tree("node-1", [2])),
            _command_result(),
            _command_result(_configured_disks([])),
            _command_result(_crush_tree()),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        assert step.run(step_context).result_type == ResultType.COMPLETED
        command = jhelper.run_cmd_on_machine_unit_payload.call_args_list[2].args[2]
        assert command == (
            "microceph disk remove osd.2 --timeout 1800 "
            "--confirm-failure-domain-downgrade"
        )

    def test_command_failure_aborts_cleanup(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(_configured_disks([{"osd": 2, "location": "node-1"}])),
            _command_result(_crush_tree("node-1", [2])),
            ExecFailedException("remove failed"),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert jhelper.run_cmd_on_machine_unit_payload.call_count == 3

    def test_invalid_listing_aborts_cleanup(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.return_value = _command_result("{}")

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert jhelper.run_cmd_on_machine_unit_payload.call_count == 1

    def test_remaining_osd_fails_verification(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper)
        configured = _configured_disks([{"osd": 2, "location": "node-1"}])
        tree = _crush_tree("node-1", [2])
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(configured),
            _command_result(tree),
            _command_result(),
            _command_result(configured),
            _command_result(tree),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED


class TestSetCephMgrPoolSizeStep:
    def test_is_skip(self, cclient, jhelper, step_context):
        cclient.cluster.list_nodes_by_role.return_value = []
        step = SetCephMgrPoolSizeStep(cclient, jhelper, "test-model")
        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_storage_nodes(self, cclient, jhelper, step_context):
        cclient.cluster.list_nodes_by_role.return_value = ["sunbeam1"]
        step = SetCephMgrPoolSizeStep(cclient, jhelper, "test-model")
        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_run(self, cclient, jhelper, step_context):
        jhelper.run_action.return_value = Mock()
        step = SetCephMgrPoolSizeStep(cclient, jhelper, "test-model")
        result = step.run(step_context)

        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_action_failed(self, cclient, jhelper, step_context):
        jhelper.run_action.side_effect = ActionFailedException("Action failed...")

        step = SetCephMgrPoolSizeStep(cclient, jhelper, "test-model")
        result = step.run(step_context)

        jhelper.run_action.assert_called_once()
        expected_message = "Action failed..."
        assert result.result_type == ResultType.FAILED
        assert result.message == expected_message
