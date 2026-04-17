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
    DeployHypervisorApplicationStep,
    ReapplyHypervisorOptionalIntegrationsStep,
    ReapplyHypervisorTerraformPlanStep,
    RemoveHypervisorUnitStep,
)
from sunbeam.storage.base import HypervisorIntegration


# Common fixtures
# Additional fixtures specific to hypervisor tests
@pytest.fixture
def read_config_patch():
    """Patch for read_config function."""
    with patch(
        "sunbeam.steps.hypervisor.read_config",
        Mock(return_value={"model": "openstack"}),
    ) as mock:
        yield mock


class TestDeployHypervisorApplicationStep:
    @pytest.fixture
    def ovn_manager(self):
        """Mock OVN manager."""
        mgr = Mock()
        mgr.get_provider.return_value = Mock()
        return mgr

    @pytest.fixture
    def deploy_hypervisor_step(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        read_config_patch,
        ovn_manager,
    ):
        """Create DeployHypervisorApplicationStep instance for testing."""
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "rabbitmq-offer-url": "rabbitmq-url",
            "keystone-offer-url": "keystone-url",
            "cert-distributor-offer-url": "cert-distributor-url",
            "ca-offer-url": "ca-url",
            "nova-offer-url": "nova-url",
        }
        basic_deployment.get_ovn_manager.return_value = ovn_manager
        basic_deployment.get_space.return_value = "test-space"
        step = DeployHypervisorApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            openstack_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )
        return step

    @patch("sunbeam.steps.hypervisor.StorageBackendManager")
    def test_extra_tfvars_no_backends(
        self,
        mock_manager_class,
        deploy_hypervisor_step,
    ):
        """extra_tfvars should have empty extra_integrations when no backends."""
        mock_manager = Mock()
        mock_manager.collect_hypervisor_integrations.return_value = set()
        mock_manager_class.return_value = mock_manager

        tfvars = deploy_hypervisor_step.extra_tfvars()

        assert "extra_integrations" in tfvars
        assert tfvars["extra_integrations"] == []
        mock_manager.collect_hypervisor_integrations.assert_called_once_with(
            deploy_hypervisor_step.deployment,
            deploy_hypervisor_step.client,
        )

    @patch("sunbeam.steps.hypervisor.StorageBackendManager")
    def test_extra_tfvars_with_integrations(
        self,
        mock_manager_class,
        deploy_hypervisor_step,
    ):
        """extra_tfvars should include integrations from storage framework."""
        mock_manager = Mock()
        mock_manager.collect_hypervisor_integrations.return_value = {
            HypervisorIntegration(
                application_name="cinder-volume-ceph",
                endpoint_name="ceph-access",
                hypervisor_endpoint_name="ceph-access",
            ),
        }
        mock_manager_class.return_value = mock_manager

        tfvars = deploy_hypervisor_step.extra_tfvars()

        assert "extra_integrations" in tfvars
        assert len(tfvars["extra_integrations"]) == 1
        integration = tfvars["extra_integrations"][0]
        assert integration["application_name"] == "cinder-volume-ceph"
        assert integration["endpoint_name"] == "ceph-access"
        assert integration["hypervisor_endpoint_name"] == "ceph-access"

    @patch("sunbeam.steps.hypervisor.StorageBackendManager")
    def test_extra_tfvars_includes_offer_urls(
        self,
        mock_manager_class,
        deploy_hypervisor_step,
    ):
        """extra_tfvars should still include Juju offer URLs."""
        mock_manager = Mock()
        mock_manager.collect_hypervisor_integrations.return_value = set()
        mock_manager_class.return_value = mock_manager

        tfvars = deploy_hypervisor_step.extra_tfvars()

        assert tfvars["rabbitmq-offer-url"] == "rabbitmq-url"
        assert tfvars["keystone-offer-url"] == "keystone-url"
        assert tfvars["cert-distributor-offer-url"] == "cert-distributor-url"
        assert tfvars["ca-offer-url"] == "ca-url"
        assert tfvars["nova-offer-url"] == "nova-url"

    @patch("sunbeam.steps.hypervisor.StorageBackendManager")
    def test_extra_tfvars_includes_integrations(
        self,
        mock_manager_class,
        deploy_hypervisor_step,
    ):
        """extra_tfvars should include extra_integrations from storage backends."""
        mock_manager = Mock()
        mock_manager.collect_hypervisor_integrations.return_value = set()
        mock_manager_class.return_value = mock_manager

        tfvars = deploy_hypervisor_step.extra_tfvars()
        assert "extra_integrations" in tfvars


class TestRemoveHypervisorUnitStep:
    @pytest.fixture
    def remove_hypervisor_step(
        self,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        basic_deployment,
        read_config_patch,
    ):
        """Create RemoveHypervisorUnitStep instance for testing."""
        return RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )

    def test_is_skip(
        self,
        remove_hypervisor_step,
        basic_client,
        basic_jhelper,
        read_config_patch,
        step_context,
    ):
        id = "1"
        basic_client.cluster.get_node_info.return_value = {"machineid": id}
        basic_jhelper.get_application.return_value = Mock(
            units={"hypervisor/1": Mock(machine=id)}
        )
        basic_jhelper.run_action.return_value = {"results": {"result": []}}

        result = remove_hypervisor_step.is_skip(step_context)

        basic_client.cluster.get_node_info.assert_called_once()
        basic_jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(
        self,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        basic_client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node missing..."
        )

        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )
        result = step.is_skip(step_context)

        basic_client.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(
        self,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )
        result = step.is_skip(step_context)

        basic_jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(
        self,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        basic_client.cluster.get_node_info.return_value = {}
        basic_jhelper.get_application.return_value = Mock(units={})

        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )
        result = step.is_skip(step_context)

        basic_client.cluster.get_node_info.assert_called_once()
        basic_jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_running_guests(
        self,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        basic_client.cluster.get_node_info.return_value = {"machineid": "1"}
        basic_jhelper.get_application.return_value = Mock(
            units={"hypervisor/1": Mock(machine="1")}
        )
        basic_jhelper.run_action.return_value = {"result": json.dumps(["1", "2"])}
        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run(
        self,
        remove_hypervisor,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )
        step.unit = "unit/1"
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        remove_hypervisor.assert_called_once_with(
            basic_jhelper, basic_deployment, "test-0"
        )

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_guests(
        self,
        remove_hypervisor,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )
        result = step.run(step_context)
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
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        basic_jhelper.run_action.return_value = {"result": json.dumps(["1", "2"])}
        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
            True,
        )
        step.unit = "unit/1"
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        remove_hypervisor.assert_called_once_with(
            basic_jhelper, basic_deployment, "test-0"
        )

    @patch("sunbeam.steps.hypervisor.remove_hypervisor")
    def test_run_application_not_found(
        self,
        remove_hypervisor,
        basic_client,
        test_name,
        basic_jhelper,
        test_model,
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        basic_jhelper.run_action.return_value = {"result": "[]"}
        basic_jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )
        step.unit = "unit/1"
        result = step.run(step_context)

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
        basic_deployment,
        read_config_patch,
        step_context,
    ):
        basic_jhelper.run_action.return_value = {"result": "[]"}
        basic_jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = RemoveHypervisorUnitStep(
            basic_client,
            basic_jhelper,
            basic_deployment,
            test_name,
            test_model,
        )
        step.unit = "unit/1"
        result = step.run(step_context)

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
        step_context,
    ):
        basic_client.cluster.list_nodes_by_role.return_value = ["node-1"]
        step = ReapplyHypervisorTerraformPlanStep(
            basic_client, basic_tfhelper, basic_jhelper, basic_manifest, test_model
        )
        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_run_pristine_installation(
        self,
        reapply_hypervisor_step,
        basic_jhelper,
        basic_tfhelper,
        step_context,
    ):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        result = reapply_hypervisor_step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        basic_jhelper.wait_until_desired_status.assert_called_once()
        call_args = basic_jhelper.wait_until_desired_status.call_args
        assert call_args.args == ("test-model", ["openstack-hypervisor"])
        assert call_args.kwargs["status"] == ["active", "unknown", "waiting"]
        assert call_args.kwargs["agent_status"] == ["idle"]
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
        step_context,
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

        basic_jhelper.get_model_owner.return_value = "test-owner"
        basic_jhelper.get_model_uuid.return_value = "test-uuid"

        result = step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()

        expected_override_tfvars: dict = {"charm_config": {}}
        expected_override_tfvars["charm_config"].update(network_config_tfvars)
        expected_override_tfvars["charm_config"].update(pci_config_tfvars)
        expected_override_tfvars["charm_config"].update(dpdk_config_tfvars)

        override_tfvars_from_mock_call = (
            basic_tfhelper.update_tfvars_and_apply_tf.call_args.kwargs.get(
                "override_tfvars", {}
            )
        )
        expected_override_tfvars["machine_model_uuid"] = "test-uuid"

        assert override_tfvars_from_mock_call == expected_override_tfvars
        assert result.result_type == ResultType.COMPLETED

    @patch("sunbeam.steps.hypervisor.StorageBackendManager")
    def test_run_refreshes_storage_hypervisor_integrations(
        self,
        mock_manager_class,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        basic_deployment,
        test_model,
        read_config_patch,
        get_network_config_patch,
        get_pci_whitelist_config_patch,
        get_dpdk_config_patch,
        step_context,
    ):
        """Reapply should keep backend-owned hypervisor integrations in tfvars."""
        mock_manager = Mock()
        mock_manager.collect_hypervisor_integrations.return_value = {
            HypervisorIntegration(
                application_name="cinder-volume-ceph",
                endpoint_name="ceph-access",
                hypervisor_endpoint_name="ceph-access",
            ),
        }
        mock_manager_class.return_value = mock_manager
        basic_jhelper.get_model_uuid.return_value = "test-uuid"
        basic_client.cluster.list_nodes_by_role.return_value = []
        step = ReapplyHypervisorTerraformPlanStep(
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
            deployment=basic_deployment,
        )

        result = step.run(step_context)

        override_tfvars = basic_tfhelper.update_tfvars_and_apply_tf.call_args.kwargs[
            "override_tfvars"
        ]
        assert override_tfvars["extra_integrations"] == [
            {
                "application_name": "cinder-volume-ceph",
                "endpoint_name": "ceph-access",
                "hypervisor_endpoint_name": "ceph-access",
            }
        ]
        mock_manager.collect_hypervisor_integrations.assert_called_once_with(
            basic_deployment,
            basic_client,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, reapply_hypervisor_step, basic_tfhelper, step_context
    ):
        basic_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        result = reapply_hypervisor_step.run(step_context)

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self, reapply_hypervisor_step, basic_jhelper, step_context
    ):
        basic_jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        result = reapply_hypervisor_step.run(step_context)

        basic_jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestReapplyHypervisorOptionalIntegrationsStep:
    def test_tf_apply_extra_args_includes_barbican(self):
        """Barbican integration target must be in the optional integrations list."""
        step = ReapplyHypervisorOptionalIntegrationsStep.__new__(
            ReapplyHypervisorOptionalIntegrationsStep
        )
        args = step.tf_apply_extra_args()
        assert "-target=juju_integration.hypervisor-barbican" in args

    def test_tf_apply_extra_args_includes_masakari(self):
        """Masakari integration target must still be present after barbican addition."""
        step = ReapplyHypervisorOptionalIntegrationsStep.__new__(
            ReapplyHypervisorOptionalIntegrationsStep
        )
        args = step.tf_apply_extra_args()
        assert "-target=juju_integration.hypervisor-masakari" in args
