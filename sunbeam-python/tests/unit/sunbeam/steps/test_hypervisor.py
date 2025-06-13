# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import unittest
from unittest.mock import Mock, patch

from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.core.terraform import TerraformException
from sunbeam.steps.hypervisor import (
    ReapplyHypervisorTerraformPlanStep,
    RemoveHypervisorUnitStep,
)


class TestRemoveHypervisorUnitStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.read_config = patch(
            "sunbeam.steps.hypervisor.read_config",
            Mock(
                return_value={
                    "openstack_model": "openstack",
                }
            ),
        )
        guest = Mock()
        type(guest).name = "my-guest"
        self.guests = [guest]

    def setUp(self):
        self.client = Mock()
        self.read_config.start()
        self.jhelper = Mock()
        self.name = "test-0"

    def tearDown(self):
        self.read_config.stop()

    def test_is_skip(self):
        id = "1"
        self.client.cluster.get_node_info.return_value = {"machineid": id}
        self.jhelper.get_application.return_value = Mock(
            units={"hypervisor/1": Mock(machine=id)}
        )
        self.jhelper.run_action.return_value = {"results": {"result": []}}

        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        result = step.is_skip()

        self.client.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self):
        self.client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node missing..."
        )

        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        result = step.is_skip()

        self.client.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(self):
        self.client.cluster.get_node_info.return_value = {}
        self.jhelper.get_application.return_value = Mock(units={})

        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        result = step.is_skip()

        self.client.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_running_guests(self):
        self.client.cluster.get_node_info.return_value = {"machineid": "1"}
        self.jhelper.get_application.return_value = Mock(
            units={"hypervisor/1": Mock(machine="1")}
        )
        self.jhelper.run_action.return_value = {"result": json.dumps(["1", "2"])}
        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run(self, remove_hypervisor):
        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        step.unit = "unit/1"
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        remove_hypervisor.assert_called_once_with("test-0", self.jhelper)

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_guests(self, remove_hypervisor):
        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert not remove_hypervisor.called

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_guests_force(self, remove_hypervisor):
        self.jhelper.run_action.return_value = {"result": json.dumps(["1", "2"])}
        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model", True
        )
        step.unit = "unit/1"
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        remove_hypervisor.assert_called_once_with("test-0", self.jhelper)

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_application_not_found(self, remove_hypervisor):
        self.jhelper.run_action.return_value = {"result": "[]"}
        self.jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        step.unit = "unit/1"
        result = step.run()

        self.jhelper.remove_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_timeout(self, remove_hypervisor):
        self.jhelper.run_action.return_value = {"result": "[]"}
        self.jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = RemoveHypervisorUnitStep(
            self.client, self.name, self.jhelper, "test-model"
        )
        step.unit = "unit/1"
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestReapplyHypervisorTerraformPlanStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.read_config = patch(
            "sunbeam.steps.hypervisor.read_config",
            Mock(
                return_value={
                    "openstack_model": "openstack",
                }
            ),
        )
        self.get_network_config = patch(
            "sunbeam.steps.hypervisor.get_external_network_configs",
            Mock(return_value={}),
        )

    def setUp(self):
        self.client = Mock()
        self.client.cluster.list_nodes_by_role.return_value = []
        self.read_config.start()
        self.get_network_config.start()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()

    def tearDown(self):
        self.read_config.stop()
        self.get_network_config.stop()

    def test_is_skip(self):
        self.client.cluster.list_nodes_by_role.return_value = ["node-1"]
        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest, "test-model"
        )
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run_pristine_installation(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest, "test-model"
        )
        result = step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    @patch("sunbeam.steps.hypervisor.get_external_network_configs")
    def test_run_after_configure_step(self, get_external_network_configs):
        # This is a case where external network configs are already added
        # and Reapply terraform plan is called.
        # Check if override_tfvars contain external network configs
        # previously added
        network_config_tfvars = {
            "external-bridge": "br-ex",
            "external-bridge-address": "172.16.2.1/24",
            "physnet-name": "physnet1",
        }
        get_external_network_configs.return_value = network_config_tfvars
        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest, "test-model"
        )
        result = step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        expected_override_tfvars = {"charm_config": network_config_tfvars}
        override_tfvars_from_mock_call = (
            self.tfhelper.update_tfvars_and_apply_tf.call_args.kwargs.get(
                "override_tfvars", {}
            )
        )
        assert override_tfvars_from_mock_call == expected_override_tfvars
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest, "test-model"
        )
        result = step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest, "test-model"
        )
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
