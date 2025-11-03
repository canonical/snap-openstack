# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.steps.microovn import (
    DeployMicroOVNApplicationStep,
    EnableMicroOVNStep,
    ReapplyMicroOVNOptionalIntegrationsStep,
)


# Additional fixtures specific to microovn tests
@pytest.fixture
def test_node():
    """Test node name."""
    return "test-node"


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
    ):
        """Create DeployMicroOVNApplicationStep instance for testing."""
        return DeployMicroOVNApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )

    def test_get_application_timeout(self, deploy_microovn_step):
        timeout = deploy_microovn_step.get_application_timeout()
        assert timeout == 1200

    def test_extra_tfvars(self, deploy_microovn_step, basic_deployment, basic_client):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "ca-offer-url": "provider:admin/default.ca",
            "ovn-relay-offer-url": "provider:admin/default.ovn-relay",
        }
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper

        network_nodes = [
            {"machineid": "1", "name": "node1"},
            {"machineid": "2", "name": "node2"},
        ]
        basic_client.cluster.list_nodes_by_role.return_value = network_nodes

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert "ca-offer-url" in extra_tfvars
        assert "ovn-relay-offer-url" in extra_tfvars
        assert "microovn_machine_ids" in extra_tfvars
        assert set(extra_tfvars["microovn_machine_ids"]) == {"1", "2"}

    def test_extra_tfvars_no_network_nodes(
        self, deploy_microovn_step, basic_deployment, basic_client
    ):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "ca-offer-url": "provider:admin/default.ca",
            "ovn-relay-offer-url": "provider:admin/default.ovn-relay",
        }
        basic_deployment.get_tfhelper.return_value = openstack_tfhelper

        basic_client.cluster.list_nodes_by_role.return_value = []

        extra_tfvars = deploy_microovn_step.extra_tfvars()

        assert "ca-offer-url" in extra_tfvars
        assert "ovn-relay-offer-url" in extra_tfvars
        assert "endpoint_bindings" in extra_tfvars
        assert extra_tfvars["ca-offer-url"] == "provider:admin/default.ca"
        assert extra_tfvars["ovn-relay-offer-url"] == "provider:admin/default.ovn-relay"


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
    ):
        """Create ReapplyMicroOVNOptionalIntegrationsStep instance for testing."""
        return ReapplyMicroOVNOptionalIntegrationsStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )

    def test_tf_apply_extra_args(self, reapply_microovn_step):
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
