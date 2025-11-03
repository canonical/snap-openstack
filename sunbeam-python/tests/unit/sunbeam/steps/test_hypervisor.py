# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, patch

import pytest

from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.core.terraform import TerraformException
from sunbeam.steps.hypervisor import (
    ReapplyHypervisorTerraformPlanStep,
    RemoveHypervisorUnitStep,
)


# Common fixtures
# Additional fixtures specific to hypervisor tests
@pytest.fixture
def read_config_patch():
    """Patch for read_config function."""
    with patch(
        "sunbeam.steps.hypervisor.read_config",
        Mock(return_value={"openstack_model": "openstack"}),
    ) as mock:
        yield mock


class TestRemoveHypervisorUnitStep:
    @pytest.fixture
    def remove_hypervisor_step(
        self, basic_client, test_name, basic_jhelper, test_model, read_config_patch
    ):
        """Create RemoveHypervisorUnitStep instance for testing."""
        return RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )

    def test_is_skip(
        self, remove_hypervisor_step, basic_client, basic_jhelper, read_config_patch
    ):
        id = "1"
        basic_client.cluster.get_node_info.return_value = {"machineid": id}
        basic_jhelper.get_application.return_value = Mock(
            units={"hypervisor/1": Mock(machine=id)}
        )
        basic_jhelper.run_action.return_value = {"results": {"result": []}}

        result = remove_hypervisor_step.is_skip()

        basic_client.cluster.get_node_info.assert_called_once()
        basic_jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(
        self, basic_client, test_name, basic_jhelper, test_model, read_config_patch
    ):
        basic_client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node missing..."
        )

        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )
        result = step.is_skip()

        basic_client.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(
        self, basic_client, test_name, basic_jhelper, test_model, read_config_patch
    ):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )
        result = step.is_skip()

        basic_jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(
        self, basic_client, test_name, basic_jhelper, test_model, read_config_patch
    ):
        basic_client.cluster.get_node_info.return_value = {}
        basic_jhelper.get_application.return_value = Mock(units={})

        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )
        result = step.is_skip()

        basic_client.cluster.get_node_info.assert_called_once()
        basic_jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_running_guests(
        self, basic_client, test_name, basic_jhelper, test_model, read_config_patch
    ):
        basic_client.cluster.get_node_info.return_value = {"machineid": "1"}
        basic_jhelper.get_application.return_value = Mock(
            units={"hypervisor/1": Mock(machine="1")}
        )
        basic_jhelper.run_action.return_value = {"result": json.dumps(["1", "2"])}
        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )
        result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run(
        self,
        remove_hypervisor,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        read_config_patch,
    ):
        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )
        step.unit = "unit/1"
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        remove_hypervisor.assert_called_once_with("test-0", basic_jhelper)

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_guests(
        self,
        remove_hypervisor,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        read_config_patch,
    ):
        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )
        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert not remove_hypervisor.called

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_guests_force(
        self,
        remove_hypervisor,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        read_config_patch,
    ):
        basic_jhelper.run_action.return_value = {"result": json.dumps(["1", "2"])}
        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model, True
        )
        step.unit = "unit/1"
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        remove_hypervisor.assert_called_once_with("test-0", basic_jhelper)

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_application_not_found(
        self,
        remove_hypervisor,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        read_config_patch,
    ):
        basic_jhelper.run_action.return_value = {"result": "[]"}
        basic_jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )
        step.unit = "unit/1"
        result = step.run()

        basic_jhelper.remove_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_timeout(
        self,
        remove_hypervisor,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        read_config_patch,
    ):
        basic_jhelper.run_action.return_value = {"result": "[]"}
        basic_jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = RemoveHypervisorUnitStep(
            basic_client, test_name, basic_jhelper, test_model
        )
        step.unit = "unit/1"
        result = step.run()

        basic_jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestReapplyHypervisorTerraformPlanStep:
    @pytest.fixture
    def get_network_config_patch(self):
        """Patch for get_external_network_configs function."""
        with patch(
            "sunbeam.steps.hypervisor.get_external_network_configs",
            Mock(return_value={}),
        ) as mock:
            yield mock

    @pytest.fixture
    def get_pci_whitelist_config_patch(self):
        """Patch for get_pci_whitelist_config function."""
        with patch(
            "sunbeam.steps.hypervisor.get_pci_whitelist_config",
            Mock(return_value={}),
        ) as mock:
            yield mock

    @pytest.fixture
    def get_dpdk_config_patch(self):
        """Patch for get_dpdk_config function."""
        with patch(
            "sunbeam.steps.hypervisor.get_dpdk_config",
            Mock(return_value={}),
        ) as mock:
            yield mock

    @pytest.fixture
    def reapply_hypervisor_step(
        self,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        read_config_patch,
        get_network_config_patch,
        get_pci_whitelist_config_patch,
        get_dpdk_config_patch,
    ):
        """Create ReapplyHypervisorTerraformPlanStep instance for testing."""
        basic_client.cluster.list_nodes_by_role.return_value = []
        return ReapplyHypervisorTerraformPlanStep(
            basic_client, basic_tfhelper, basic_jhelper, basic_manifest, test_model
        )

    def test_is_skip(
        self,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        read_config_patch,
        get_network_config_patch,
        get_pci_whitelist_config_patch,
        get_dpdk_config_patch,
    ):
        basic_client.cluster.list_nodes_by_role.return_value = ["node-1"]
        step = ReapplyHypervisorTerraformPlanStep(
            basic_client, basic_tfhelper, basic_jhelper, basic_manifest, test_model
        )
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run_pristine_installation(
        self, reapply_hypervisor_step, basic_jhelper, basic_tfhelper
    ):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        result = reapply_hypervisor_step.run()

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    @patch("sunbeam.steps.hypervisor.get_external_network_configs")
    @patch("sunbeam.steps.hypervisor.get_pci_whitelist_config")
    @patch("sunbeam.steps.hypervisor.get_dpdk_config")
    def test_run_after_configure_step(
        self,
        get_dpdk_config,
        get_pci_whitelist_config,
        get_external_network_configs,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        read_config_patch,
    ):
        # This is a case where external network configs are already added
        # and Reapply terraform plan is called.
        # Check if override_tfvars contain external network configs
        # previously added
        network_config_tfvars = {
            "external-bridge": "br-ex",
            "external-bridge-address": "172.16.2.1/24",
            "physnet-name": "physnet1",
        }
        pci_config_tfvars = {
            "pci-device-specs": '[{"vendor_id": "8086", "product_id": "1563", "physical_network": "physnet1"}]'
        }
        dpdk_config_tfvars = {
            "dpdk-enabled": False,
            "dpdk-datapath-cores": 0,
            "dpdk-controlplane-cores": 0,
            "dpdk-memory": 0,
            "dpdk-driver": "vfio-pci",
        }
        get_external_network_configs.return_value = network_config_tfvars
        get_pci_whitelist_config.return_value = pci_config_tfvars
        get_dpdk_config.return_value = dpdk_config_tfvars
        # Configure the mock to return an empty list for storage nodes
        basic_client.cluster.list_nodes_by_role.return_value = []
        step = ReapplyHypervisorTerraformPlanStep(
            basic_client, basic_tfhelper, basic_jhelper, basic_manifest, test_model
        )
        result = step.run()

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()

        expected_override_tfvars = {"charm_config": {}}
        expected_override_tfvars["charm_config"].update(network_config_tfvars)
        expected_override_tfvars["charm_config"].update(pci_config_tfvars)
        expected_override_tfvars["charm_config"].update(dpdk_config_tfvars)

        override_tfvars_from_mock_call = (
            basic_tfhelper.update_tfvars_and_apply_tf.call_args.kwargs.get(
                "override_tfvars", {}
            )
        )
        assert override_tfvars_from_mock_call == expected_override_tfvars
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self, reapply_hypervisor_step, basic_tfhelper):
        basic_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        result = reapply_hypervisor_step.run()

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self, reapply_hypervisor_step, basic_jhelper):
        basic_jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        result = reapply_hypervisor_step.run()

        basic_jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
