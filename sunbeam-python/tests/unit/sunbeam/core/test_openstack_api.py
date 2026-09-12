# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import sunbeam.core.openstack_api

FAKE_CREDS = {
    "OS_AUTH_URL": "http://10.20.21.12:80/openstack-keystone",
    "OS_USERNAME": "admin",
    "OS_PASSWORD": "fake",
    "OS_PROJECT_DOMAIN_NAME": "admin_domain",
    "OS_USER_DOMAIN_NAME": "admin_domain",
    "OS_PROJECT_NAME": "admin",
    "OS_IDENTITY_API_VERSION": "3",
    "OS_AUTH_VERSION": "3",
    "OS_REGION_NAME": "RegionTwo",
}


@pytest.fixture()
def retrieve_admin_credentials():
    with patch.object(sunbeam.core.openstack_api, "retrieve_admin_credentials") as p:
        p.return_value = FAKE_CREDS
        yield p


@pytest.fixture()
def os_connect():
    with patch.object(sunbeam.core.openstack_api.openstack, "connect") as p:
        yield p


@pytest.fixture()
def get_admin_connection():
    with patch.object(sunbeam.core.openstack_api, "get_admin_connection") as p:
        yield p


@pytest.fixture()
def remove_compute_service():
    with patch.object(sunbeam.core.openstack_api, "remove_compute_service") as p:
        yield p


@pytest.fixture()
def remove_network_service():
    with patch.object(sunbeam.core.openstack_api, "remove_network_service") as p:
        yield p


class TestOpenStackAPI:
    def test_get_admin_connection(self, retrieve_admin_credentials, os_connect):
        jhelper = Mock()
        deployment = Mock()
        sunbeam.core.openstack_api.get_admin_connection(jhelper, deployment)
        os_connect.assert_called_once_with(
            auth_url=FAKE_CREDS.get("OS_AUTH_URL"),
            username=FAKE_CREDS.get("OS_USERNAME"),
            password=FAKE_CREDS.get("OS_PASSWORD"),
            project_name=FAKE_CREDS.get("OS_PROJECT_NAME"),
            user_domain_name=FAKE_CREDS.get("OS_USER_DOMAIN_NAME"),
            project_domain_name=FAKE_CREDS.get("OS_PROJECT_DOMAIN_NAME"),
            region_name=FAKE_CREDS.get("OS_REGION_NAME"),
        )

    def test_guests_on_hypervisor(self):
        conn = Mock()
        get_admin_connection.return_value = conn
        conn.compute.servers.return_value = [1]
        assert sunbeam.core.openstack_api.guests_on_hypervisor("hyper1", conn) == [1]
        conn.compute.servers.assert_called_once_with(
            all_projects=True, hypervisor_hostname="hyper1", status=None
        )

    @pytest.mark.parametrize(
        ("node_name", "hypervisor_names", "expected"),
        [
            ("cloud-1", ["cloud-1.maas"], "cloud-1.maas"),
            ("cloud-1.example.com", ["cloud-1.example.com"], "cloud-1.example.com"),
        ],
    )
    def test_resolve_hypervisor_name(self, node_name, hypervisor_names, expected):
        conn = Mock()
        conn.compute.hypervisors.return_value = [
            SimpleNamespace(name=name) for name in hypervisor_names
        ]

        assert (
            sunbeam.core.openstack_api.resolve_hypervisor_name(node_name, conn)
            == expected
        )

    @pytest.mark.parametrize(
        "hypervisor_names", [[], ["cloud-1.a", "cloud-1.b"], ["cloud-11.maas"]]
    )
    def test_resolve_hypervisor_name_rejects_missing_or_ambiguous(
        self, hypervisor_names
    ):
        conn = Mock()
        conn.compute.hypervisors.return_value = [
            SimpleNamespace(name=name) for name in hypervisor_names
        ]

        with pytest.raises(
            ValueError, match="Cannot identify a unique Nova hypervisor"
        ):
            sunbeam.core.openstack_api.resolve_hypervisor_name("cloud-1", conn)

    def test_remove_compute_service(self):
        service1 = Mock(binary="nova-compute", host="hyper1")
        conn = Mock()
        conn.compute.services.return_value = [service1]
        sunbeam.core.openstack_api.remove_compute_service("hyper1", conn)
        conn.compute.disable_service.assert_called_once_with(service1)
        conn.compute.delete_service.assert_called_once_with(service1)

    def test_remove_network_service(self):
        service1 = Mock(binary="ovn-controller", host="hyper1")
        conn = Mock()
        conn.network.agents.return_value = [service1]
        sunbeam.core.openstack_api.remove_network_service("hyper1", conn)
        conn.network.delete_agent.assert_called_once_with(service1)

    def test_remove_hypervisor(
        self, get_admin_connection, remove_compute_service, remove_network_service
    ):
        conn = Mock()
        jhelper = Mock()
        deployment = Mock()
        get_admin_connection.return_value = conn
        sunbeam.core.openstack_api.remove_hypervisor(jhelper, deployment, "hyper1")
        get_admin_connection.assert_called_once_with(jhelper, deployment)
        remove_compute_service.assert_called_once_with("hyper1", conn)
        remove_network_service.assert_called_once_with("hyper1", conn)
