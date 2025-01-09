# Copyright (c) 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import itertools
from unittest.mock import Mock, call

import pytest

from sunbeam.provider.maintenance import checks


@pytest.fixture
def mock_conn():
    return Mock()


@pytest.fixture
def mock_get_admin_connection(mock_conn, mocker):
    return mocker.patch.object(checks, "get_admin_connection", return_value=mock_conn)


@pytest.fixture
def mock_guests_on_hypervisor(mocker):
    return mocker.patch.object(checks, "guests_on_hypervisor")


class TestInstancesStatusCheck:
    def test_run(
        self,
        mock_conn,
        mock_get_admin_connection,
        mock_guests_on_hypervisor,
    ):
        nodes = ["node1", "node2"]
        instances = [[Mock(), Mock(), Mock()], [Mock(), Mock(), Mock()]]

        mock_guests_on_hypervisor.side_effect = instances

        check = checks.InstancesStatusCheck(Mock(), nodes, False)
        assert check.run()

    @pytest.mark.parametrize("inst_status", [("ERROR"), ("MIGRATING")])
    def test_run_failed(
        self,
        inst_status,
        mock_conn,
        mock_get_admin_connection,
        mock_guests_on_hypervisor,
    ):
        nodes = ["node1", "node2"]
        instances = [[Mock(), Mock(), Mock()], [Mock(), Mock(), Mock()]]
        instances[-1][-1].status = inst_status
        instances[-1][-1].id = "target-inst"

        mock_guests_on_hypervisor.side_effect = instances

        check = checks.InstancesStatusCheck(Mock(), nodes, False)
        assert not check.run()
        assert check.message == f"Instance target-inst is in {inst_status} status"

    @pytest.mark.parametrize("inst_status", [("ERROR"), ("MIGRATING")])
    def test_run_failed_focce(
        self,
        inst_status,
        mock_conn,
        mock_get_admin_connection,
        mock_guests_on_hypervisor,
    ):
        nodes = ["node1", "node2"]
        instances = [[Mock(), Mock(), Mock()], [Mock(), Mock(), Mock()]]
        instances[-1][-1].status = inst_status
        instances[-1][-1].id = "target-inst"

        mock_guests_on_hypervisor.side_effect = instances

        check = checks.InstancesStatusCheck(Mock(), nodes, True)

        assert check.run()


class TestNoEphemeralDiskCheck:
    def test_run(self, mocker):
        mock_conn = Mock()
        nodes = ["node1", "node2"]
        instances = [[Mock(), Mock(), Mock()], [Mock(), Mock(), Mock()]]
        mocker.patch.object(checks, "get_admin_connection", return_value=mock_conn)
        mock_guests_on_hypervisor = mocker.patch.object(
            checks, "guests_on_hypervisor", side_effect=instances
        )

        flavors = [Mock(), Mock(), Mock(), Mock(), Mock(), Mock()]
        for flavor in flavors:
            flavor.ephemeral = 0
        mock_conn.compute.find_flavor.side_effect = flavors

        check = checks.NoEphemeralDiskCheck(Mock(), nodes, False)
        assert check.run()
        mock_guests_on_hypervisor.assert_has_calls(
            [
                call(hypervisor_name="node1", conn=mock_conn),
                call(hypervisor_name="node2", conn=mock_conn),
            ]
        )
        mock_conn.compute.find_flavor.assert_has_calls(
            [
                call(inst.flavor.get.return_value)
                for inst in itertools.chain.from_iterable(instances)
            ]
        )

    def test_run_failed(self, mocker):
        mock_conn = Mock()
        nodes = ["node1", "node2"]
        instances = [[Mock(), Mock(), Mock()], [Mock(), Mock(), Mock()]]
        instances[-1][-1].id = "target-inst"
        mocker.patch.object(checks, "get_admin_connection", return_value=mock_conn)
        mock_guests_on_hypervisor = mocker.patch.object(
            checks, "guests_on_hypervisor", side_effect=instances
        )

        flavors = [Mock(), Mock(), Mock(), Mock(), Mock(), Mock()]
        for flavor in flavors:
            flavor.ephemeral = 0
        flavors[-1].ephemeral = 100
        mock_conn.compute.find_flavor.side_effect = flavors

        check = checks.NoEphemeralDiskCheck(Mock(), nodes, False)
        assert not check.run()
        assert check.message == "Instance target-inst has ephemeral disk"
        mock_guests_on_hypervisor.assert_has_calls(
            [
                call(hypervisor_name="node1", conn=mock_conn),
                call(hypervisor_name="node2", conn=mock_conn),
            ]
        )
        mock_conn.compute.find_flavor.assert_has_calls(
            [
                call(inst.flavor.get.return_value)
                for inst in itertools.chain.from_iterable(instances)
            ]
        )

    def test_run_force(self, mocker):
        mock_conn = Mock()
        nodes = ["node1", "node2"]
        instances = [[Mock(), Mock(), Mock()], [Mock(), Mock(), Mock()]]
        mocker.patch.object(checks, "get_admin_connection", return_value=mock_conn)
        mock_guests_on_hypervisor = mocker.patch.object(
            checks, "guests_on_hypervisor", side_effect=instances
        )

        flavors = [Mock(), Mock(), Mock(), Mock(), Mock(), Mock()]
        for flavor in flavors:
            flavor.ephemeral = 0
        flavors[-1].ephemeral = 100
        mock_conn.compute.find_flavor.side_effect = flavors

        check = checks.NoEphemeralDiskCheck(Mock(), nodes, True)
        assert check.run()
        mock_guests_on_hypervisor.assert_has_calls(
            [
                call(hypervisor_name="node1", conn=mock_conn),
                call(hypervisor_name="node2", conn=mock_conn),
            ]
        )
        mock_conn.compute.find_flavor.assert_has_calls(
            [
                call(inst.flavor.get.return_value)
                for inst in itertools.chain.from_iterable(instances)
            ]
        )


class TestNoInstanceOnNodeCheck:
    def test_run(self, mock_conn, mock_get_admin_connection, mock_guests_on_hypervisor):
        node = "node1"
        instances = []
        mock_guests_on_hypervisor.return_value = instances

        check = checks.NoInstancesOnNodeCheck(Mock(), node, False)
        assert check.run()
        mock_guests_on_hypervisor.assert_called_once_with(
            hypervisor_name=node,
            conn=mock_conn,
        )

    def test_run_failed(
        self, mock_conn, mock_get_admin_connection, mock_guests_on_hypervisor
    ):
        node = "node1"
        instances = [Mock(), Mock()]
        instances[0].id = "inst-0"
        instances[1].id = "inst-1"
        mock_guests_on_hypervisor.return_value = instances

        check = checks.NoInstancesOnNodeCheck(Mock(), node, False)
        assert not check.run()
        mock_guests_on_hypervisor.assert_called_once_with(
            hypervisor_name=node,
            conn=mock_conn,
        )
        assert check.message == f"Instances inst-0,inst-1 still on node {node}"

    def test_run_force(
        self, mock_conn, mock_get_admin_connection, mock_guests_on_hypervisor
    ):
        node = "node1"
        instances = [Mock(), Mock()]
        instances[0].id = "inst-0"
        instances[1].id = "inst-1"
        mock_guests_on_hypervisor.return_value = instances

        check = checks.NoInstancesOnNodeCheck(Mock(), node, True)
        assert check.run()
        mock_guests_on_hypervisor.assert_called_once_with(
            hypervisor_name="node1",
            conn=mock_conn,
        )


class TestNovaInDisableStatusCheck:
    def test_run(self, mock_conn, mock_get_admin_connection):
        services = [Mock(), Mock(), Mock()]
        services[-1].host = "node1"
        services[-1].binary = "nova-compute"
        services[-1].status = "disabled"
        mock_conn.compute.services.return_value = services

        check = checks.NovaInDisableStatusCheck(Mock(), "node1", False)
        assert check.run()

    def test_run_failed(self, mock_conn, mock_get_admin_connection):
        services = [Mock(), Mock(), Mock()]
        services[-1].host = "node1"
        services[-1].binary = "nova-compute"
        services[-1].status = "enabled"
        mock_conn.compute.services.return_value = services

        check = checks.NovaInDisableStatusCheck(Mock(), "node1", False)
        assert not check.run()
        assert check.message == "Nova compute still enabled on node node1"

    def test_run_force(self, mock_conn, mock_get_admin_connection):
        services = [Mock(), Mock(), Mock()]
        services[-1].host = "node1"
        services[-1].binary = "nova-compute"
        services[-1].status = "enabled"
        mock_conn.compute.services.return_value = services

        check = checks.NovaInDisableStatusCheck(Mock(), "node1", True)
        assert check.run()
