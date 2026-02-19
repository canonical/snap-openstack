# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import typing

from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.core.juju import JujuHelper
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.lazy import LazyImport

if typing.TYPE_CHECKING:
    import openstack
else:
    openstack = LazyImport("openstack")

LOG = logging.getLogger(__name__)


def get_admin_connection(jhelper: JujuHelper) -> "openstack.connection.Connection":
    """Return a connection to keystone using admin credentials.

    :param jhelper: Juju helpers for retrieving admin credentials
    :raises: openstack.exceptions.SDKException
    """
    admin_auth_info = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    conn = openstack.connect(
        auth_url=admin_auth_info.get("OS_AUTH_URL"),
        username=admin_auth_info.get("OS_USERNAME"),
        password=admin_auth_info.get("OS_PASSWORD"),
        project_name=admin_auth_info.get("OS_PROJECT_NAME"),
        user_domain_name=admin_auth_info.get("OS_USER_DOMAIN_NAME"),
        project_domain_name=admin_auth_info.get("OS_PROJECT_DOMAIN_NAME"),
    )
    return conn


def guests_on_hypervisor(
    hypervisor_name: str,
    conn: "openstack.connection.Connection",
    status: str | None = None,
) -> list["openstack.compute.v2.server.Server"]:
    """Return a list of guests that run on the given hypervisor.

    :param hypervisor_name: Name of hypervisor
    :param conn: Admin connection
    :param status: server status
    :raises: openstack.exceptions.SDKException
    """
    return list(
        conn.compute.servers(
            all_projects=True, hypervisor_hostname=hypervisor_name, status=status
        )
    )


def remove_compute_service(
    hypervisor_name: str, conn: "openstack.connection.Connection"
) -> None:
    """Remove compute services associated with hypervisor from nova.

    :param hypervisor_name: Name of hypervisor
    :param conn: Admin connection
    """
    for service in conn.compute.services(host=hypervisor_name):
        LOG.info(f"Disabling {service.binary} on {service.host}")
        conn.compute.disable_service(service)
        conn.compute.delete_service(service)


def remove_network_service(
    hypervisor_name: str, conn: "openstack.connection.Connection"
) -> None:
    """Remove network services associated with hypervisor from neutron.

    :param hypervisor_name: Name of hypervisor
    :param conn: Admin connection
    """
    for service in conn.network.agents(host=hypervisor_name):
        LOG.info(f"Disabling {service.binary} on {service.host}")
        conn.network.delete_agent(service)


def remove_hypervisor(hypervisor_name: str, jhelper: JujuHelper) -> None:
    """Remove services associated with hypervisor from OpenStack.

    :param hypervisor_name: Name of hypervisor
    :param jhelper: Juju helpers for retrieving admin credentials
    """
    conn = get_admin_connection(jhelper)
    remove_compute_service(hypervisor_name, conn)
    remove_network_service(hypervisor_name, conn)
