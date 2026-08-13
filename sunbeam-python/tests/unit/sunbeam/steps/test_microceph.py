# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import MagicMock, Mock

from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.core.common import Result, ResultType, run_plan
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    ExecFailedException,
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


def _crush_tree(children=None):
    nodes = (
        []
        if children is None
        else [{"name": "node-1", "type": "host", "children": children}]
    )
    return json.dumps({"nodes": nodes})


def _unit(machine, workload="active", agent="idle"):
    return Mock(
        machine=machine,
        workload_status=Mock(current=workload),
        juju_status=Mock(current=agent),
    )


def _cleanup_step(cclient, jhelper, force=False):
    cclient.cluster.get_node_info.return_value = {
        "machineid": "1",
        "role": "storage",
    }
    jhelper.get_machines.return_value = {
        "1": Mock(hostname="node-1"),
        "2": Mock(hostname="node-2"),
    }
    jhelper.get_application.return_value = Mock(
        units={"microceph/0": _unit("1"), "microceph/1": _unit("2")}
    )
    return RemoveMicrocephOSDsStep(
        cclient,
        "node-1",
        jhelper,
        "test-model",
        force=force,
    )


def _run_cleanup_plan(step):
    console = MagicMock()
    follow_up = Mock()
    follow_up.name = "Follow up"
    follow_up.status = "Following up ..."
    follow_up.has_prompts.return_value = False
    follow_up.is_skip.return_value = Result(ResultType.COMPLETED)
    follow_up.run.return_value = Result(ResultType.COMPLETED)

    run_plan([step, follow_up], console)

    return follow_up


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
    def test_prefers_healthy_surviving_unit(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper)
        jhelper.get_machines.return_value["3"] = Mock(hostname="node-3")
        jhelper.get_application.return_value = Mock(
            units={
                "microceph/0": _unit("1"),
                "microceph/1": _unit("2", workload="blocked"),
                "microceph/2": _unit("3"),
            }
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.unit == "microceph/2"

    def test_prefers_healthy_target_over_unhealthy_survivor(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        jhelper.get_application.return_value = Mock(
            units={
                "microceph/0": _unit("1"),
                "microceph/1": _unit("2", workload="blocked"),
            }
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.unit == "microceph/0"

    def test_read_falls_back_after_exec_failure(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper)
        exec_error = ExecFailedException("exec failed")
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            exec_error,
            _command_result(stdout=_configured_disks([])),
            _command_result(stdout=_crush_tree()),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.unit == "microceph/0"
        assert [
            call.args[0]
            for call in jhelper.run_cmd_on_machine_unit_payload.call_args_list
        ] == ["microceph/1", "microceph/0", "microceph/0"]

    def test_remove_does_not_fall_back_after_exec_failure(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        exec_error = ExecFailedException("exec failed")
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                stdout=_configured_disks(
                    [{"osd": 2, "location": "node-1", "path": "/dev/sdb"}]
                )
            ),
            _command_result(stdout=_crush_tree([2])),
            exec_error,
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert [
            call.args[0]
            for call in jhelper.run_cmd_on_machine_unit_payload.call_args_list
        ] == ["microceph/1", "microceph/1", "microceph/1"]

    def test_configured_disk_uses_only_cleanup_fields(self):
        disks = RemoveMicrocephOSDsStep._parse_configured_disks(
            _configured_disks(
                [
                    {
                        "osd": 2,
                        "location": "node-1",
                        "serial": "disk-2",
                    }
                ]
            )
        )

        assert disks[0]["osd"] == 2

    def test_removed_target_reaches_follow_up_step(self, cclient, jhelper):
        cclient.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "node removed"
        )
        jhelper.get_machines.return_value = {"2": Mock(hostname="node-2")}
        jhelper.get_application.return_value = Mock(units={"microceph/1": _unit("2")})
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(stdout=_configured_disks([])),
            _command_result(stdout=json.dumps({"nodes": []})),
        ]
        step = RemoveMicrocephOSDsStep(cclient, "node-1", jhelper, "test-model")

        follow_up = _run_cleanup_plan(step)

        follow_up.run.assert_called_once()
        assert step.unit == "microceph/1"

    def test_forced_last_unit_retry_reaches_follow_up_step(self, cclient, jhelper):
        cclient.cluster.get_node_info.return_value = {
            "machineid": "1",
            "role": "storage",
        }
        jhelper.get_machines.return_value = {"1": Mock(hostname="node-1")}
        jhelper.get_application.return_value = Mock(units={})
        step = RemoveMicrocephOSDsStep(
            cclient, "node-1", jhelper, "test-model", force=True
        )

        follow_up = _run_cleanup_plan(step)

        follow_up.run.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_not_called()

    def test_missing_application_is_skipped(self, cclient, jhelper, step_context):
        cclient.cluster.get_node_info.return_value = {
            "machineid": "1",
            "role": "storage",
        }
        jhelper.get_machines.return_value = {"1": Mock(hostname="node-1")}
        jhelper.get_application.side_effect = ApplicationNotFoundException(
            "application removed"
        )
        step = RemoveMicrocephOSDsStep(
            cclient, "node-1", jhelper, "test-model", force=True
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        jhelper.run_cmd_on_machine_unit_payload.assert_not_called()

    def test_non_storage_role_still_removes_actual_osd(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        cclient.cluster.get_node_info.return_value["role"] = "compute"
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                stdout=_configured_disks(
                    [{"osd": 2, "location": "node-1", "path": "/dev/sdb"}]
                )
            ),
            _command_result(stdout=_crush_tree([2])),
            _command_result(),
            _command_result(stdout=_configured_disks([])),
            _command_result(stdout=_crush_tree()),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        assert step.run(step_context).result_type == ResultType.COMPLETED
        commands = [
            call.args[2]
            for call in jhelper.run_cmd_on_machine_unit_payload.call_args_list
        ]
        assert "microceph disk remove osd.2 --timeout 1800" in commands

    def test_run_removes_sorted_db_osds_and_verifies_both_sources(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                stdout=_configured_disks(
                    [
                        {"osd": 5, "location": "node-1", "path": "/dev/sdc"},
                        {"osd": 2, "location": "node-1", "path": "/dev/sdb"},
                    ]
                )
            ),
            _command_result(
                stdout=json.dumps(
                    {"nodes": [{"name": "node-1", "type": "host", "children": [5, 2]}]}
                )
            ),
            _command_result(),
            _command_result(),
            _command_result(stdout=_configured_disks([])),
            _command_result(
                stdout=json.dumps(
                    {"nodes": [{"name": "node-1", "type": "host", "children": []}]}
                )
            ),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
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

    def test_crush_only_osd_fails_before_any_removal(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(stdout=_configured_disks([])),
            _command_result(
                stdout=json.dumps(
                    {"nodes": [{"name": "node-1", "type": "host", "children": [7]}]}
                )
            ),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert jhelper.run_cmd_on_machine_unit_payload.call_count == 2

    def test_db_and_crush_mismatch_fails_before_any_removal(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                stdout=_configured_disks(
                    [{"osd": 2, "location": "node-1", "path": "/dev/sdb"}]
                )
            ),
            _command_result(
                stdout=json.dumps(
                    {"nodes": [{"name": "node-1", "type": "host", "children": [2, 7]}]}
                )
            ),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert jhelper.run_cmd_on_machine_unit_payload.call_count == 2

    def test_force_keeps_safety_flags_only_on_remove_command(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper, force=True)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                stdout=_configured_disks(
                    [{"osd": 2, "location": "node-1", "path": "/dev/sdb"}]
                )
            ),
            _command_result(
                stdout=json.dumps(
                    {"nodes": [{"name": "node-1", "type": "host", "children": [2]}]}
                )
            ),
            _command_result(),
            _command_result(stdout=_configured_disks([])),
            _command_result(
                stdout=json.dumps(
                    {"nodes": [{"name": "node-1", "type": "host", "children": []}]}
                )
            ),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        assert step.run(step_context).result_type == ResultType.COMPLETED
        command = jhelper.run_cmd_on_machine_unit_payload.call_args_list[2].args[2]
        assert command == (
            "microceph disk remove osd.2 --timeout 1800 "
            "--confirm-failure-domain-downgrade --bypass-safety-checks"
        )

    def test_force_does_not_ignore_exec_failure(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper, force=True)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                stdout=_configured_disks(
                    [{"osd": 2, "location": "node-1", "path": "/dev/sdb"}]
                )
            ),
            _command_result(
                stdout=json.dumps(
                    {"nodes": [{"name": "node-1", "type": "host", "children": [2]}]}
                )
            ),
            ExecFailedException("transport failed"),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        assert step.run(step_context).result_type == ResultType.FAILED

    def test_last_target_unit_is_allowed_as_cleanup_fallback(
        self, cclient, jhelper, step_context
    ):
        cclient.cluster.get_node_info.return_value = {
            "machineid": "1",
            "role": "storage",
        }
        jhelper.get_machines.return_value = {"1": Mock(hostname="node-1")}
        jhelper.get_application.return_value = Mock(units={"microceph/0": _unit("1")})
        step = RemoveMicrocephOSDsStep(cclient, "node-1", jhelper, "test-model")

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        assert step.unit == "microceph/0"

    def test_no_unit_fails_closed(self, cclient, jhelper, step_context):
        cclient.cluster.get_node_info.return_value = {
            "machineid": "1",
            "role": "storage",
        }
        jhelper.get_machines.return_value = {"1": Mock(hostname="node-1")}
        jhelper.get_application.return_value = Mock(units={})
        step = RemoveMicrocephOSDsStep(cclient, "node-1", jhelper, "test-model")

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.FAILED

    def test_unknown_machine_fails_closed(self, cclient, jhelper, step_context):
        cclient.cluster.get_node_info.return_value = {
            "machineid": "1",
            "role": "storage",
        }
        jhelper.get_machines.return_value = {"1": Mock(hostname="node-1")}
        jhelper.get_application.return_value = Mock(
            units={"microceph/0": _unit("unknown")}
        )
        step = RemoveMicrocephOSDsStep(cclient, "node-1", jhelper, "test-model")

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.FAILED

    def test_unknown_machine_does_not_block_known_surviving_unit(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        jhelper.get_application.return_value = Mock(
            units={
                "microceph/0": _unit("1"),
                "microceph/1": _unit("2"),
                "microceph/2": _unit("unknown"),
            }
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.unit == "microceph/1"

    def test_db_only_osd_is_removed_when_crush_host_is_absent(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                stdout=_configured_disks(
                    [{"osd": 2, "location": "node-1", "path": "/dev/sdb"}]
                )
            ),
            _command_result(stdout=_crush_tree()),
            _command_result(),
            _command_result(stdout=_configured_disks([])),
            _command_result(stdout=_crush_tree()),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        commands = [
            call.args[2]
            for call in jhelper.run_cmd_on_machine_unit_payload.call_args_list
        ]
        assert "microceph disk remove osd.2 --timeout 1800" in commands
        assert not any(
            "purge" in command or "crush remove" in command for command in commands
        )

    def test_malformed_json_fails_before_removal(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.return_value = _command_result(
            stdout="not-json"
        )

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert jhelper.run_cmd_on_machine_unit_payload.call_count == 1

    def test_remaining_db_and_crush_osds_fail_after_removal(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        configured = _configured_disks(
            [{"osd": 2, "location": "node-1", "path": "/dev/sdb"}]
        )
        tree = _crush_tree([2])
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(stdout=configured),
            _command_result(stdout=tree),
            _command_result(),
            _command_result(stdout=configured),
            _command_result(stdout=tree),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        commands = [
            call.args[2]
            for call in jhelper.run_cmd_on_machine_unit_payload.call_args_list
        ]
        assert commands.count("microceph disk remove osd.2 --timeout 1800") == 1

    def test_partial_retry_removes_only_remaining_db_osd(
        self, cclient, jhelper, step_context
    ):
        step = _cleanup_step(cclient, jhelper)
        configured = _configured_disks(
            [
                {"osd": 2, "location": "node-1", "path": "/dev/sdb"},
                {"osd": 5, "location": "node-1", "path": "/dev/sdc"},
            ]
        )
        tree = _crush_tree([2, 5])
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(stdout=configured),
            _command_result(stdout=tree),
            _command_result(),
            ExecFailedException("busy"),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        assert step.run(step_context).result_type == ResultType.FAILED

        retry = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.reset_mock()
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(
                stdout=_configured_disks(
                    [{"osd": 5, "location": "node-1", "path": "/dev/sdc"}]
                )
            ),
            _command_result(stdout=_crush_tree([5])),
            _command_result(),
            _command_result(stdout=_configured_disks([])),
            _command_result(stdout=_crush_tree()),
        ]

        assert retry.is_skip(step_context).result_type == ResultType.COMPLETED
        assert retry.run(step_context).result_type == ResultType.COMPLETED
        commands = [
            call.args[2]
            for call in jhelper.run_cmd_on_machine_unit_payload.call_args_list
        ]
        assert commands.count("microceph disk remove osd.5 --timeout 1800") == 1
        assert "microceph disk remove osd.2 --timeout 1800" not in commands

    def test_clean_state_is_idempotent(self, cclient, jhelper, step_context):
        step = _cleanup_step(cclient, jhelper)
        jhelper.run_cmd_on_machine_unit_payload.side_effect = [
            _command_result(stdout=_configured_disks([])),
            _command_result(stdout=json.dumps({"nodes": []})),
        ]

        assert step.is_skip(step_context).result_type == ResultType.COMPLETED
        assert step.run(step_context).result_type == ResultType.COMPLETED
        assert [
            call.args[2]
            for call in jhelper.run_cmd_on_machine_unit_payload.call_args_list
        ] == [
            "microceph disk list --json",
            "microceph.ceph osd tree --format json",
        ]


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
