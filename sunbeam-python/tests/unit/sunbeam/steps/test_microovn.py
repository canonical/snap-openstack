# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import Mock

from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.steps.microovn import (
    DeployMicroOVNApplicationStep,
    EnableMicroOVNStep,
    ReapplyMicroOVNOptionalIntegrationsStep,
)


class TestDeployMicroOVNApplicationStep(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.deployment = Mock()
        self.model = "test-model"

    def test_get_application_timeout(self):
        step = DeployMicroOVNApplicationStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            self.model,
        )
        timeout = step.get_application_timeout()
        assert timeout == 1200

    def test_extra_tfvars(self):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "ca-offer-url": "provider:admin/default.ca",
            "ovn-relay-offer-url": "provider:admin/default.ovn-relay",
        }
        self.deployment.get_tfhelper.return_value = openstack_tfhelper

        network_nodes = [
            {"machineid": "1", "name": "node1"},
            {"machineid": "2", "name": "node2"},
        ]
        self.client.cluster.list_nodes_by_role.return_value = network_nodes

        step = DeployMicroOVNApplicationStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            self.model,
        )
        extra_tfvars = step.extra_tfvars()

        assert "ca-offer-url" in extra_tfvars
        assert "ovn-relay-offer-url" in extra_tfvars
        assert "microovn_machine_ids" in extra_tfvars
        assert set(extra_tfvars["microovn_machine_ids"]) == {"1", "2"}

    def test_extra_tfvars_no_network_nodes(self):
        openstack_tfhelper = Mock()
        openstack_tfhelper.output.return_value = {
            "ca-offer-url": "provider:admin/default.ca",
            "ovn-relay-offer-url": "provider:admin/default.ovn-relay",
        }
        self.deployment.get_tfhelper.return_value = openstack_tfhelper

        self.client.cluster.list_nodes_by_role.return_value = []

        step = DeployMicroOVNApplicationStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            self.model,
        )
        extra_tfvars = step.extra_tfvars()

        expected_tfvars = {
            "ca-offer-url": "provider:admin/default.ca",
            "ovn-relay-offer-url": "provider:admin/default.ovn-relay",
        }
        assert extra_tfvars == expected_tfvars


class TestReapplyMicroOVNOptionalIntegrationsStep(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()
        self.deployment = Mock()
        self.model = "test-model"

    def test_tf_apply_extra_args(self):
        step = ReapplyMicroOVNOptionalIntegrationsStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            self.model,
        )
        extra_args = step.tf_apply_extra_args()

        expected_args = [
            "-target=juju_integration.microovn-microcluster-token-distributor",
            "-target=juju_integration.microovn-certs",
            "-target=juju_integration.microovn-ovsdb-cms",
            "-target=juju_integration.microovn-openstack-network-agents",
        ]
        assert extra_args == expected_args


class TestEnableMicroOVNStep(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.jhelper = Mock()
        self.model = "test-model"
        self.node = "test-node"

    def test_is_skip_node_not_exist(self):
        self.client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node does not exist"
        )

        step = EnableMicroOVNStep(self.client, self.node, self.jhelper, self.model)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_not_found(self):
        self.client.cluster.get_node_info.return_value = {"machineid": "1"}
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application not found"
        )

        step = EnableMicroOVNStep(self.client, self.node, self.jhelper, self.model)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED
        assert result.message == "microovn application has not been deployed yet"

    def test_is_skip_unit_not_on_machine(self):
        self.client.cluster.get_node_info.return_value = {"machineid": "1"}
        self.jhelper.get_application.return_value = Mock(
            units={"microovn/0": Mock(machine="2")}
        )

        step = EnableMicroOVNStep(self.client, self.node, self.jhelper, self.model)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_success(self):
        self.client.cluster.get_node_info.return_value = {"machineid": "1"}
        self.jhelper.get_application.return_value = Mock(
            units={"microovn/0": Mock(machine="1")}
        )

        step = EnableMicroOVNStep(self.client, self.node, self.jhelper, self.model)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED
        assert step.unit == "microovn/0"

    def test_run_success(self):
        step = EnableMicroOVNStep(self.client, self.node, self.jhelper, self.model)
        step.unit = "microovn/0"

        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_no_unit(self):
        step = EnableMicroOVNStep(self.client, self.node, self.jhelper, self.model)
        step.unit = None

        result = step.run()

        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit not found on machine"
