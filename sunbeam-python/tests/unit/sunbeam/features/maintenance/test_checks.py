# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import Mock, call, patch

import pytest

from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.watcher import WATCHER_APPLICATION
from sunbeam.features.maintenance import checks


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
        node = "node1"
        instances = [[], [], []]
        mock_guests_on_hypervisor.side_effect = instances

        check = checks.InstancesStatusCheck(Mock(), node, False)
        assert check.run()

    def test_run_failed(
        self,
        mock_conn,
        mock_get_admin_connection,
        mock_guests_on_hypervisor,
    ):
        node = "node1"
        instances = [[Mock()], [Mock()], [Mock()]]
        instances[0][-1].id = "target-inst-1"
        instances[1][-1].id = "target-inst-2"
        instances[2][-1].id = "target-inst-3"

        mock_guests_on_hypervisor.side_effect = instances

        check = checks.InstancesStatusCheck(Mock(), node, False)
        assert not check.run()
        assert check.message == (
            "Instances not in expected status: {}".format(
                {
                    "target-inst-1": "ERROR",
                    "target-inst-2": "MIGRATING",
                    "target-inst-3": "SHUTOFF",
                },
            )
        )

    def test_run_failed_force(
        self,
        mock_conn,
        mock_get_admin_connection,
        mock_guests_on_hypervisor,
    ):
        nodes = "node1"
        instances = [[Mock()], [Mock()], [Mock(), Mock()]]
        instances[-1][-1].id = "target-inst"

        mock_guests_on_hypervisor.side_effect = instances

        check = checks.InstancesStatusCheck(Mock(), nodes, True)

        assert check.run()


class TestNoEphemeralDiskCheck:
    def test_run(self, mocker):
        mock_conn = Mock()
        node = "node1"
        instances = [Mock(), Mock(), Mock()]
        mocker.patch.object(checks, "get_admin_connection", return_value=mock_conn)
        mock_guests_on_hypervisor = mocker.patch.object(
            checks, "guests_on_hypervisor", return_value=instances
        )

        mock_flavor = Mock()
        mock_flavor.ephemeral = 0
        mock_conn.compute.find_flavor.return_value = mock_flavor

        check = checks.NoEphemeralDiskCheck(Mock(), node, False)
        assert check.run()
        mock_guests_on_hypervisor.assert_has_calls(
            [
                call(hypervisor_name="node1", conn=mock_conn),
            ]
        )
        mock_conn.compute.find_flavor.assert_has_calls(
            [call(inst.flavor.get.return_value) for inst in instances]
        )

    def test_run_failed(self, mocker):
        mock_conn = Mock()
        node = "node1"
        instances = [Mock(), Mock(), Mock()]
        instances[-1].id = "target-inst-a"
        instances[-2].id = "target-inst-b"
        mocker.patch.object(checks, "get_admin_connection", return_value=mock_conn)
        mock_guests_on_hypervisor = mocker.patch.object(
            checks, "guests_on_hypervisor", return_value=instances
        )

        flavors = [Mock(), Mock(), Mock()]
        for flavor in flavors:
            flavor.ephemeral = 0
        flavors[-1].ephemeral = 100
        flavors[-2].ephemeral = 100
        mock_conn.compute.find_flavor.side_effect = flavors

        check = checks.NoEphemeralDiskCheck(Mock(), node, False)
        assert not check.run()
        assert check.message == "Instances have ephemeral disk: {}".format(
            ["target-inst-b", "target-inst-a"]
        )
        mock_guests_on_hypervisor.assert_has_calls(
            [
                call(hypervisor_name="node1", conn=mock_conn),
            ]
        )
        mock_conn.compute.find_flavor.assert_has_calls(
            [call(inst.flavor.get.return_value) for inst in instances]
        )

    def test_run_force(self, mocker):
        mock_conn = Mock()
        node = "node1"
        instances = [Mock(), Mock(), Mock()]
        mocker.patch.object(checks, "get_admin_connection", return_value=mock_conn)
        mock_guests_on_hypervisor = mocker.patch.object(
            checks, "guests_on_hypervisor", return_value=instances
        )

        flavors = [Mock(), Mock(), Mock()]
        for flavor in flavors:
            flavor.ephemeral = 0
        flavors[-1].ephemeral = 100
        mock_conn.compute.find_flavor.side_effect = flavors

        check = checks.NoEphemeralDiskCheck(Mock(), node, True)
        assert check.run()
        mock_guests_on_hypervisor.assert_has_calls(
            [
                call(hypervisor_name="node1", conn=mock_conn),
            ]
        )
        mock_conn.compute.find_flavor.assert_has_calls(
            [call(inst.flavor.get.return_value) for inst in instances]
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
        services = [Mock()]
        mock_conn.compute.services.return_value = services

        check = checks.NovaInDisableStatusCheck(Mock(), "node1", False)
        assert check.run()

    def test_run_failed(self, mock_conn, mock_get_admin_connection):
        services = []
        mock_conn.compute.services.return_value = services

        check = checks.NovaInDisableStatusCheck(Mock(), "node1", False)
        assert not check.run()
        assert check.message == "Nova compute still not disabled on node node1"

    def test_run_force(self, mock_conn, mock_get_admin_connection):
        services = []
        mock_conn.compute.services.return_value = services

        check = checks.NovaInDisableStatusCheck(Mock(), "node1", True)
        assert check.run()


class TestMicroCephMaintenancePreflightCheck:
    @patch("sunbeam.features.maintenance.checks.JujuActionHelper")
    def test_run(self, mock_action_helper):
        mock_client = Mock()
        mock_jhelper = Mock()
        check = checks.MicroCephMaintenancePreflightCheck(
            mock_client,
            mock_jhelper,
            "fake-model",
            "fake-node",
            {"k1": "v1", "k2": "v2"},
            True,
        )
        check.run()
        mock_action_helper.run_action.assert_called_once_with(
            client=mock_client,
            jhelper=mock_jhelper,
            model="fake-model",
            node="fake-node",
            app="microceph",
            action_name="enter-maintenance",
            action_params={
                "k1": "v1",
                "k2": "v2",
                "dry-run": False,
                "check-only": True,
            },
        )


class TestWatcherApplicationExistsCheck:
    def test_run(self):
        mock_jhelper = Mock()

        check = checks.WatcherApplicationExistsCheck(mock_jhelper)
        result = check.run()
        assert result
        mock_jhelper.get_application.assert_called_once_with(
            name=WATCHER_APPLICATION,
            model=OPENSTACK_MODEL,
        )

    def test_run_failed(self):
        mock_jhelper = Mock()
        mock_jhelper.get_application.side_effect = ApplicationNotFoundException
        check = checks.WatcherApplicationExistsCheck(mock_jhelper)
        result = check.run()
        assert result is False
