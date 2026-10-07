# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

from sunbeam.core.common import ResultType
from sunbeam.core.juju import ActionFailedException
from sunbeam.steps.microceph import (
    ConfigureMicrocephOSDStep,
    DeployMicrocephApplicationStep,
    SetCephMgrPoolSizeStep,
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


class TestDeployMicrocephApplicationStep:
    @staticmethod
    def _build_step(cclient, deployment, tfhelper, jhelper, manifest):
        # Each network resolves to its own name, so the assertions below read as
        # "endpoint X is bound to network Y".
        deployment.get_space.side_effect = lambda network: network.value
        deployment.get_tfhelper.return_value.output.return_value = {}
        cclient.cluster.list_nodes_by_role.return_value = ["node-1"]
        return DeployMicrocephApplicationStep(
            deployment, cclient, tfhelper, jhelper, manifest, "test-model"
        )

    @patch("sunbeam.steps.microceph.read_config", return_value={})
    def test_extra_tfvars_binds_nfs_to_storage(
        self, read_config, cclient, deployment, tfhelper, jhelper, manifest
    ):
        step = self._build_step(cclient, deployment, tfhelper, jhelper, manifest)

        bindings = step.extra_tfvars()["endpoint_bindings"]

        nfs_bindings = [b for b in bindings if b.get("endpoint") == "nfs"]
        assert nfs_bindings == [{"endpoint": "nfs", "space": "storage"}]

    @patch("sunbeam.steps.microceph.read_config", return_value={})
    def test_extra_tfvars_binds_nfs_to_same_space_as_public(
        self, read_config, cclient, deployment, tfhelper, jhelper, manifest
    ):
        # NFS keeps listening on the network it already used (the "public"
        # endpoint's), so declaring the binding does not move it.
        step = self._build_step(cclient, deployment, tfhelper, jhelper, manifest)

        bindings = {
            b.get("endpoint"): b["space"]
            for b in step.extra_tfvars()["endpoint_bindings"]
        }

        assert bindings["nfs"] == bindings["public"]

    @patch("sunbeam.steps.microceph.read_config", return_value={})
    def test_extra_tfvars_keeps_existing_bindings(
        self, read_config, cclient, deployment, tfhelper, jhelper, manifest
    ):
        step = self._build_step(cclient, deployment, tfhelper, jhelper, manifest)

        bindings = {
            b.get("endpoint"): b["space"]
            for b in step.extra_tfvars()["endpoint_bindings"]
        }

        # The None key is the application default binding (no endpoint named).
        assert bindings == {
            None: "management",
            "admin": "management",
            "peers": "management",
            "cluster": "storage-cluster",
            "public": "storage",
            "ceph": "storage",
            "mds": "storage",
            "radosgw": "storage",
            "nfs": "storage",
        }
