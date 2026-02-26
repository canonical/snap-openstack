# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.core import ovn
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.steps.microovn import (
    DeployMicroOVNApplicationStep,
    EnableMicroOVNStep,
    ReapplyMicroOVNOptionalIntegrationsStep,
    ReapplyMicroOVNTerraformPlanStep,
    SetOvnProviderStep,
)


# Additional fixtures specific to microovn tests
@pytest.fixture
def test_node():
    """Test node name."""
    return "test-node"


@pytest.fixture
def ovn_manager():
    mock_manager = Mock()
    mock_manager.get_roles_for_microovn.return_value = []
    yield mock_manager


class TestDeployMicroOVNApplicationStep:
    @pytest.fixture
    def deploy_microovn_step(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ovn_manager,
    ):
        """Create DeployMicroOVNApplicationStep instance for testing."""
        return DeployMicroOVNApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
            ovn_manager,
        )

    def test_get_application_timeout(self, deploy_microovn_step):
        timeout = deploy_microovn_step.get_application_timeout()
        assert timeout == 1200

    def test_extra_tfvars(
        self, deploy_microovn_step, basic_deployment, basic_client, ovn_manager
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "ca-offer-url": "provider:admin/default.ca",
        }
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        ovn_manager.get_machines.return_value = ["1", "2"]

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert "ca-offer-url" in extra_tfvars
        assert "microovn_machine_ids" in extra_tfvars
        assert set(extra_tfvars["microovn_machine_ids"]) == {"1", "2"}
        assert extra_tfvars["token_distributor_machine_ids"] == ["1"]

    def test_extra_tfvars_no_network_nodes(
        self, deploy_microovn_step, basic_deployment, basic_client, ovn_manager
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "ca-offer-url": "provider:admin/default.ca",
        }
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        ovn_manager.get_machines.return_value = []

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert "ca-offer-url" in extra_tfvars
        assert "endpoint_bindings" in extra_tfvars
        assert extra_tfvars["ca-offer-url"] == "provider:admin/default.ca"


class TestReapplyMicroOVNOptionalIntegrationsStep:
    @pytest.fixture
    def reapply_microovn_step(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ovn_manager,
    ):
        """Create ReapplyMicroOVNOptionalIntegrationsStep instance for testing."""
        return ReapplyMicroOVNOptionalIntegrationsStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
            ovn_manager,
        )

    def test_tf_apply_extra_args(self, reapply_microovn_step):
        reapply_microovn_step.tfhelper.output.return_value = {}
        extra_args = reapply_microovn_step.tf_apply_extra_args()

        expected_args = [
            "-target=juju_integration.microovn-microcluster-token-distributor",
            "-target=juju_integration.microovn-certs",
            "-target=juju_integration.microovn-ovsdb-cms",
            "-target=juju_integration.microovn-openstack-network-agents",
        ]
        assert extra_args == expected_args


class TestEnableMicroOVNStep:
    @pytest.fixture
    def enable_microovn_step(self, basic_client, test_node, basic_jhelper, test_model):
        """Create EnableMicroOVNStep instance for testing."""
        return EnableMicroOVNStep(basic_client, test_node, basic_jhelper, test_model)

    def test_is_skip_node_not_exist(self, basic_client, enable_microovn_step):
        basic_client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node does not exist"
        )

        result = enable_microovn_step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_not_found(
        self, basic_client, basic_jhelper, enable_microovn_step
    ):
        basic_client.cluster.get_node_info.return_value = {"machineid": "1"}
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application not found"
        )

        result = enable_microovn_step.is_skip()

        assert result.result_type == ResultType.SKIPPED
        assert result.message == "microovn application has not been deployed yet"

    def test_is_skip_unit_not_on_machine(
        self, basic_client, basic_jhelper, enable_microovn_step
    ):
        basic_client.cluster.get_node_info.return_value = {"machineid": "1"}
        basic_jhelper.get_application.return_value = Mock(
            units={"microovn/0": Mock(machine="2")}
        )

        result = enable_microovn_step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_success(self, basic_client, basic_jhelper, enable_microovn_step):
        basic_client.cluster.get_node_info.return_value = {"machineid": "1"}
        basic_jhelper.get_application.return_value = Mock(
            units={"microovn/0": Mock(machine="1")}
        )

        result = enable_microovn_step.is_skip()

        assert result.result_type == ResultType.COMPLETED
        assert enable_microovn_step.unit == "microovn/0"

    def test_run_success(self, enable_microovn_step):
        enable_microovn_step.unit = "microovn/0"

        result = enable_microovn_step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_no_unit(self, enable_microovn_step):
        enable_microovn_step.unit = None

        result = enable_microovn_step.run()

        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit not found on machine"


class TestReapplyMicroOVNTerraformPlanStep:
    @pytest.fixture
    def reapply_microovn_terraform_step(
        self,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
    ):
        manager = Mock()
        manager.get_roles_for_microovn.return_value = {ovn.Role.NETWORK}
        return ReapplyMicroOVNTerraformPlanStep(
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
            manager,
        )

    def test_is_skip_skipped_when_no_matching_nodes(
        self, reapply_microovn_terraform_step, basic_client
    ):
        basic_client.cluster.list_nodes_by_role.return_value = []

        result = reapply_microovn_terraform_step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_completed_when_matching_nodes(
        self, reapply_microovn_terraform_step, basic_client
    ):
        basic_client.cluster.list_nodes_by_role.return_value = [{"name": "node-1"}]

        result = reapply_microovn_terraform_step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run_success(
        self,
        reapply_microovn_terraform_step,
        basic_tfhelper,
        basic_jhelper,
        basic_client,
    ):
        with patch(
            "sunbeam.steps.microovn.get_external_network_configs",
            return_value={"external-bridge-address": "10.0.0.1/24"},
        ):
            result = reapply_microovn_terraform_step.run()

        assert result.result_type == ResultType.COMPLETED
        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        basic_jhelper.wait_application_ready.assert_called_once()
        assert (
            reapply_microovn_terraform_step.extra_tfvars[
                "charm_openstack_network_agents_config"
            ]["external-bridge-address"]
            == "10.0.0.1/24"
        )


class TestSetOvnProviderStep:
    def test_is_skip(self, basic_client):
        """Test is_skip method."""
        step = SetOvnProviderStep(basic_client, Mock())
        with patch.object(step, "get_config_from_snap") as mock_get_config:
            mock_get_config.return_value = ovn.OvnProvider.OVN_K8S
            with patch("sunbeam.core.ovn.load_provider_config") as mock_load:
                mock_load.return_value = ovn.OvnConfig(provider=ovn.OvnProvider.OVN_K8S)
                result = step.is_skip()
                assert result.result_type == ResultType.SKIPPED

    def test_is_skip_not_bootstrapped(self, basic_client):
        step = SetOvnProviderStep(basic_client, Mock())
        with patch.object(step, "get_config_from_snap") as mock_get_config:
            mock_get_config.return_value = ovn.OvnProvider.MICROOVN
            with patch("sunbeam.core.ovn.load_provider_config") as mock_load:
                mock_load.return_value = ovn.OvnConfig(provider=ovn.OvnProvider.OVN_K8S)
                with patch.object(
                    basic_client.cluster, "check_sunbeam_bootstrapped"
                ) as mock_bootstrapped:
                    mock_bootstrapped.return_value = False
                    result = step.is_skip()
                    assert result.result_type == ResultType.COMPLETED

    def test_is_skip_bootstrapped_change_provider(self, basic_client):
        step = SetOvnProviderStep(basic_client, Mock())
        with patch.object(step, "get_config_from_snap") as mock_get_config:
            mock_get_config.return_value = ovn.OvnProvider.MICROOVN
            with patch("sunbeam.core.ovn.load_provider_config") as mock_load:
                mock_load.return_value = ovn.OvnConfig(provider=ovn.OvnProvider.OVN_K8S)
                with patch.object(
                    basic_client.cluster, "check_sunbeam_bootstrapped"
                ) as mock_bootstrapped:
                    mock_bootstrapped.return_value = True
                    result = step.is_skip()
                    assert result.result_type == ResultType.FAILED

    def test_run(self, basic_client):
        """Test run method."""
        step = SetOvnProviderStep(basic_client, Mock())
        step.wanted_provider = ovn.OvnProvider.OVN_K8S
        with patch("sunbeam.core.ovn.load_provider_config") as mock_load:
            mock_config = ovn.OvnConfig(provider=None)
            mock_load.return_value = mock_config
            with patch("sunbeam.core.ovn.write_provider_config") as mock_write:
                result = step.run()
                assert result.result_type == ResultType.COMPLETED
                mock_write.assert_called_once_with(basic_client, mock_config)
                assert mock_config.provider == ovn.OvnProvider.OVN_K8S
