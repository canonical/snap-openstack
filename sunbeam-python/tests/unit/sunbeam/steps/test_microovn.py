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
    mock_manager.get_token_distributor_machines.return_value = []
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

    def test_get_accepted_application_status_allows_blocked_for_ovn_k8s(
        self, deploy_microovn_step, ovn_manager
    ):
        ovn_manager.get_provider.return_value = ovn.OvnProvider.OVN_K8S

        statuses = deploy_microovn_step.get_accepted_application_status()

        assert statuses == ["active", "unknown", "blocked"]

    def test_get_accepted_application_status_excludes_blocked_for_microovn_provider(
        self, deploy_microovn_step, ovn_manager
    ):
        ovn_manager.get_provider.return_value = ovn.OvnProvider.MICROOVN

        statuses = deploy_microovn_step.get_accepted_application_status()

        assert statuses == ["active", "unknown"]

    def test_extra_tfvars(
        self, deploy_microovn_step, basic_deployment, basic_client, ovn_manager
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "ca-offer-url": "provider:admin/default.ca",
        }
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        ovn_manager.get_machines_by_architecture.return_value = {
            ovn.DEFAULT_ARCHITECTURE: ["1", "2"],
        }
        ovn_manager.get_token_distributor_machines.return_value = ["1"]

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert "ca-offer-url" in extra_tfvars
        assert extra_tfvars["microovn_machine_ids_by_architecture"] == {
            ovn.DEFAULT_ARCHITECTURE: ["1", "2"],
        }
        assert extra_tfvars["token_distributor_machine_ids"] == ["1"]

    def test_extra_tfvars_arm64_dpu(
        self,
        deploy_microovn_step,
        basic_deployment,
        basic_client,
        basic_jhelper,
        ovn_manager,
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {}
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        ovn_manager.get_machines_by_architecture.return_value = {
            ovn.DEFAULT_ARCHITECTURE: ["1", "2"],
            ovn.ARM64_ARCHITECTURE: ["52"],
        }
        ovn_manager.get_token_distributor_machines.return_value = ["1"]
        basic_jhelper.get_available_charm_revisions.return_value = {
            ovn.DEFAULT_ARCHITECTURE: 119,
            ovn.ARM64_ARCHITECTURE: 120,
        }

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert extra_tfvars["microovn_machine_ids_by_architecture"] == {
            ovn.DEFAULT_ARCHITECTURE: ["1", "2"],
            ovn.ARM64_ARCHITECTURE: ["52"],
        }
        assert extra_tfvars["token_distributor_machine_ids"] == ["1"]
        assert extra_tfvars["charm_openstack_network_agents_arm64_revision"] == 120

    def test_extra_tfvars_arm64_only(
        self, deploy_microovn_step, basic_deployment, basic_jhelper, ovn_manager
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {}
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        ovn_manager.get_machines_by_architecture.return_value = {
            ovn.ARM64_ARCHITECTURE: ["52"],
        }
        ovn_manager.get_token_distributor_machines.return_value = ["0"]
        basic_jhelper.get_available_charm_revisions.return_value = {
            ovn.ARM64_ARCHITECTURE: 120,
        }

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert extra_tfvars["microovn_machine_ids_by_architecture"] == {
            ovn.ARM64_ARCHITECTURE: ["52"],
        }
        assert extra_tfvars["token_distributor_machine_ids"] == ["0"]
        assert extra_tfvars["charm_openstack_network_agents_arm64_revision"] == 120

    def test_extra_tfvars_arm64_uses_manifest_channel(
        self,
        deploy_microovn_step,
        basic_deployment,
        basic_jhelper,
        basic_manifest,
        ovn_manager,
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {}
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        ovn_manager.get_machines_by_architecture.return_value = {
            ovn.ARM64_ARCHITECTURE: ["52"],
        }
        ovn_manager.get_token_distributor_machines.return_value = ["0"]
        # Channel configured for openstack-network-agents in the manifest.
        agent_manifest = Mock()
        agent_manifest.channel = "2026.1/edge"
        agent_manifest.config = None
        basic_manifest.core.software.charms.get.return_value = agent_manifest
        basic_jhelper.get_available_charm_revisions.return_value = {
            ovn.ARM64_ARCHITECTURE: 120,
        }

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        basic_jhelper.get_available_charm_revisions.assert_called_once_with(
            "openstack-network-agents", "2026.1/edge"
        )
        assert extra_tfvars["charm_openstack_network_agents_arm64_revision"] == 120

    def test_extra_tfvars_non_default_architecture(
        self, deploy_microovn_step, basic_deployment, ovn_manager
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {}
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        ovn_manager.get_machines_by_architecture.return_value = {
            "s390x": ["72"],
        }
        ovn_manager.get_token_distributor_machines.return_value = ["0"]

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert extra_tfvars["microovn_machine_ids_by_architecture"] == {
            "s390x": ["72"],
        }
        assert extra_tfvars["token_distributor_machine_ids"] == ["0"]

    def test_extra_tfvars_no_network_nodes(
        self, deploy_microovn_step, basic_deployment, basic_client, ovn_manager
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "ca-offer-url": "provider:admin/default.ca",
        }
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        ovn_manager.get_machines_by_architecture.return_value = {}

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert "ca-offer-url" in extra_tfvars
        assert "endpoint_bindings" in extra_tfvars
        assert extra_tfvars["ca-offer-url"] == "provider:admin/default.ca"
        assert extra_tfvars["microovn_machine_ids_by_architecture"] == {}
        assert extra_tfvars["token_distributor_machine_ids"] == []

    def test_extra_tfvars_network_agents_endpoint_bindings(
        self, deploy_microovn_step, basic_deployment, ovn_manager
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {}
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper
        basic_deployment.get_space.side_effect = lambda n: f"space-{n.value}"
        ovn_manager.get_machines_by_architecture.return_value = {}

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        bindings = extra_tfvars["openstack_network_agents_endpoint_bindings"]
        assert {"space": "space-management"} in bindings
        assert {"endpoint": "data", "space": "space-data"} in bindings

    def test_applications_to_wait_arm64_only(self, deploy_microovn_step, ovn_manager):
        ovn_manager.get_machines_by_architecture.return_value = {
            ovn.DEFAULT_ARCHITECTURE: [],
            ovn.ARM64_ARCHITECTURE: ["54"],
        }

        assert deploy_microovn_step._applications_to_wait() == ["microovn-arm64"]

    def test_applications_to_wait_amd64_and_arm64(
        self, deploy_microovn_step, ovn_manager
    ):
        ovn_manager.get_machines_by_architecture.return_value = {
            ovn.DEFAULT_ARCHITECTURE: ["1", "2"],
            ovn.ARM64_ARCHITECTURE: ["54"],
        }

        assert deploy_microovn_step._applications_to_wait() == [
            "microovn",
            "microovn-arm64",
        ]

    def test_applications_to_wait_non_default_architecture(
        self, deploy_microovn_step, ovn_manager
    ):
        ovn_manager.get_machines_by_architecture.return_value = {"s390x": ["72"]}

        assert deploy_microovn_step._applications_to_wait() == ["microovn-s390x"]


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
            "-target=juju_integration.microovn_arm64_microcluster_token_distributor",
            "-target=juju_integration.microovn_arm64_certs",
            "-target=juju_integration.microovn_arm64_ovsdb_cms",
            "-target=juju_integration.role-distributor-microovn",
        ]
        assert extra_args == expected_args


class TestEnableMicroOVNStep:
    @pytest.fixture
    def enable_microovn_step(self, basic_client, test_node, basic_jhelper, test_model):
        """Create EnableMicroOVNStep instance for testing."""
        return EnableMicroOVNStep(basic_client, test_node, basic_jhelper, test_model)

    def test_is_skip_node_not_exist(
        self, basic_client, enable_microovn_step, step_context
    ):
        basic_client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node does not exist"
        )

        result = enable_microovn_step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_not_found(
        self,
        basic_client,
        basic_jhelper,
        enable_microovn_step,
        step_context,
    ):
        basic_client.cluster.get_node_info.return_value = {"machineid": "1"}
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application not found"
        )

        result = enable_microovn_step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert result.message == "microovn application has not been deployed yet"

    def test_is_skip_arm64_application_not_found(
        self,
        basic_client,
        basic_jhelper,
        enable_microovn_step,
        step_context,
    ):
        basic_client.cluster.get_node_info.return_value = {
            "machineid": "52",
            "arch": "arm64",
        }
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application not found"
        )

        result = enable_microovn_step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert result.message == "microovn-arm64 application has not been deployed yet"

    def test_is_skip_success_arm64(
        self, basic_client, basic_jhelper, enable_microovn_step, step_context
    ):
        basic_client.cluster.get_node_info.return_value = {
            "machineid": "52",
            "arch": "arm64",
        }
        basic_jhelper.get_application.return_value = Mock(
            units={"microovn-arm64/0": Mock(machine="52")}
        )

        result = enable_microovn_step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert enable_microovn_step.unit == "microovn-arm64/0"

    def test_is_skip_success_non_default_architecture(
        self, basic_client, basic_jhelper, enable_microovn_step, step_context
    ):
        basic_client.cluster.get_node_info.return_value = {
            "machineid": "72",
            "arch": "s390x",
        }
        basic_jhelper.get_application.return_value = Mock(
            units={"microovn-s390x/0": Mock(machine="72")}
        )

        result = enable_microovn_step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.get_application.assert_called_once_with(
            "microovn-s390x", "test-model"
        )
        assert enable_microovn_step.unit == "microovn-s390x/0"

    def test_is_skip_unit_not_on_machine(
        self,
        basic_client,
        basic_jhelper,
        enable_microovn_step,
        step_context,
    ):
        basic_client.cluster.get_node_info.return_value = {"machineid": "1"}
        basic_jhelper.get_application.return_value = Mock(
            units={"microovn/0": Mock(machine="2")}
        )

        result = enable_microovn_step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_success(
        self, basic_client, basic_jhelper, enable_microovn_step, step_context
    ):
        basic_client.cluster.get_node_info.return_value = {"machineid": "1"}
        basic_jhelper.get_application.return_value = Mock(
            units={"microovn/0": Mock(machine="1")}
        )

        result = enable_microovn_step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert enable_microovn_step.unit == "microovn/0"

    def test_run_success(self, enable_microovn_step, step_context):
        enable_microovn_step.unit = "microovn/0"

        result = enable_microovn_step.run(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_run_no_unit(self, enable_microovn_step, step_context):
        enable_microovn_step.unit = None

        result = enable_microovn_step.run(step_context)

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
        manager.get_machines_by_architecture.return_value = {
            ovn.DEFAULT_ARCHITECTURE: ["1"],
            ovn.ARM64_ARCHITECTURE: [],
        }
        return ReapplyMicroOVNTerraformPlanStep(
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
            manager,
        )

    def test_is_skip_skipped_when_no_matching_nodes(
        self,
        reapply_microovn_terraform_step,
        basic_client,
        step_context,
    ):
        basic_client.cluster.list_nodes_by_role.return_value = []

        result = reapply_microovn_terraform_step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_completed_when_matching_nodes(
        self,
        reapply_microovn_terraform_step,
        basic_client,
        step_context,
    ):
        basic_client.cluster.list_nodes_by_role.return_value = [{"name": "node-1"}]

        result = reapply_microovn_terraform_step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_run_success(
        self,
        reapply_microovn_terraform_step,
        basic_tfhelper,
        basic_jhelper,
        basic_client,
        step_context,
    ):
        with patch(
            "sunbeam.steps.microovn.get_external_network_configs",
            return_value={"external-bridge-address": "10.0.0.1/24"},
        ):
            result = reapply_microovn_terraform_step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        basic_jhelper.wait_application_ready.assert_called_once()
        assert (
            reapply_microovn_terraform_step.extra_tfvars[
                "charm_openstack_network_agents_config"
            ]["external-bridge-address"]
            == "10.0.0.1/24"
        )

    def test_run_allows_blocked_status_for_ovn_k8s(
        self,
        reapply_microovn_terraform_step,
        basic_jhelper,
        step_context,
    ):
        reapply_microovn_terraform_step.ovn_manager.get_provider.return_value = (
            ovn.OvnProvider.OVN_K8S
        )

        with patch(
            "sunbeam.steps.microovn.get_external_network_configs", return_value={}
        ):
            result = reapply_microovn_terraform_step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.wait_application_ready.assert_called_once_with(
            "microovn",
            "test-model",
            accepted_status=["active", "unknown", "blocked"],
            timeout=1200,
        )

    def test_run_excludes_blocked_status_for_microovn_provider(
        self,
        reapply_microovn_terraform_step,
        basic_jhelper,
        step_context,
    ):
        reapply_microovn_terraform_step.ovn_manager.get_provider.return_value = (
            ovn.OvnProvider.MICROOVN
        )

        with patch(
            "sunbeam.steps.microovn.get_external_network_configs", return_value={}
        ):
            result = reapply_microovn_terraform_step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.wait_application_ready.assert_called_once_with(
            "microovn",
            "test-model",
            accepted_status=["active", "unknown"],
            timeout=1200,
        )


class TestSetOvnProviderStep:
    def test_get_config_from_snap_provider_not_set(self, basic_client):
        """Test get_config_from_snap when no provider is configured."""
        mock_snap = Mock()
        mock_snap.config.get.return_value = None
        step = SetOvnProviderStep(basic_client, mock_snap)

        result = step.get_config_from_snap(mock_snap)

        assert result == ovn.DEFAULT_PROVIDER
        mock_snap.config.get.assert_called_once_with(ovn.SNAP_PROVIDER_CONFIG_KEY)

    def test_get_config_from_snap_provider_microovn(self, basic_client):
        """Test get_config_from_snap when provider is set to microovn."""
        mock_snap = Mock()
        mock_snap.config.get.return_value = ovn.OvnProvider.MICROOVN
        step = SetOvnProviderStep(basic_client, mock_snap)

        result = step.get_config_from_snap(mock_snap)

        assert result == ovn.OvnProvider.MICROOVN
        mock_snap.config.get.assert_called_once_with(ovn.SNAP_PROVIDER_CONFIG_KEY)

    def test_get_config_from_snap_unknown_config_key(self, basic_client):
        """Test get_config_from_snap when snap config key doesn't exist."""
        from snaphelpers import UnknownConfigKey

        mock_snap = Mock()
        mock_snap.config.get.side_effect = UnknownConfigKey("ovn.provider")
        step = SetOvnProviderStep(basic_client, mock_snap)

        result = step.get_config_from_snap(mock_snap)

        assert result == ovn.DEFAULT_PROVIDER

    def test_get_config_from_snap_invalid_provider_value(self, basic_client):
        """Test get_config_from_snap with invalid provider value raises error."""
        mock_snap = Mock()
        mock_snap.config.get.return_value = "invalid-provider"
        step = SetOvnProviderStep(basic_client, mock_snap)

        with pytest.raises(ValueError) as exc_info:
            step.get_config_from_snap(mock_snap)
        assert "Invalid value 'invalid-provider'" in str(exc_info.value)
        assert "ovn.provider" in str(exc_info.value)
        assert "Valid values are:" in str(exc_info.value)

    def test_is_skip(self, basic_client, step_context):
        """Test is_skip method."""
        step = SetOvnProviderStep(basic_client, Mock())
        with patch.object(step, "get_config_from_snap") as mock_get_config:
            mock_get_config.return_value = ovn.OvnProvider.OVN_K8S
            with patch("sunbeam.core.ovn.load_provider_config") as mock_load:
                mock_load.return_value = ovn.OvnConfig(provider=ovn.OvnProvider.OVN_K8S)
                result = step.is_skip(step_context)
                assert result.result_type == ResultType.SKIPPED

    def test_is_skip_not_bootstrapped(self, basic_client, step_context):
        step = SetOvnProviderStep(basic_client, Mock())
        with patch.object(step, "get_config_from_snap") as mock_get_config:
            mock_get_config.return_value = ovn.OvnProvider.MICROOVN
            with patch("sunbeam.core.ovn.load_provider_config") as mock_load:
                mock_load.return_value = ovn.OvnConfig(provider=ovn.OvnProvider.OVN_K8S)
                with patch.object(
                    basic_client.cluster, "check_sunbeam_bootstrapped"
                ) as mock_bootstrapped:
                    mock_bootstrapped.return_value = False
                    result = step.is_skip(step_context)
                    assert result.result_type == ResultType.COMPLETED

    def test_is_skip_bootstrapped_change_provider(self, basic_client, step_context):
        step = SetOvnProviderStep(basic_client, Mock())
        with patch.object(step, "get_config_from_snap") as mock_get_config:
            mock_get_config.return_value = ovn.OvnProvider.MICROOVN
            with patch("sunbeam.core.ovn.load_provider_config") as mock_load:
                mock_load.return_value = ovn.OvnConfig(provider=ovn.OvnProvider.OVN_K8S)
                with patch.object(
                    basic_client.cluster, "check_sunbeam_bootstrapped"
                ) as mock_bootstrapped:
                    mock_bootstrapped.return_value = True
                    result = step.is_skip(step_context)
                    assert result.result_type == ResultType.FAILED

    def test_is_skip_invalid_provider(self, basic_client, step_context):
        """Test is_skip returns FAILED when invalid provider is configured."""
        step = SetOvnProviderStep(basic_client, Mock())
        with patch.object(step, "get_config_from_snap") as mock_get_config:
            mock_get_config.side_effect = ValueError(
                "Invalid value 'bad-provider' for ovn.provider. Valid values are: ovn-k8s, microovn"
            )
            result = step.is_skip(step_context)
            assert result.result_type == ResultType.FAILED
            assert "Invalid value 'bad-provider'" in result.message

    def test_run(self, basic_client, step_context):
        """Test run method."""
        step = SetOvnProviderStep(basic_client, Mock())
        step.wanted_provider = ovn.OvnProvider.OVN_K8S
        with patch("sunbeam.core.ovn.load_provider_config") as mock_load:
            mock_config = ovn.OvnConfig(provider=None)
            mock_load.return_value = mock_config
            with patch("sunbeam.core.ovn.write_provider_config") as mock_write:
                result = step.run(step_context)
                assert result.result_type == ResultType.COMPLETED
                mock_write.assert_called_once_with(basic_client, mock_config)
                assert mock_config.provider == ovn.OvnProvider.OVN_K8S
