# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import typing

from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.lazy import LazyImport

if typing.TYPE_CHECKING:
    import openstack
else:
    openstack = LazyImport("openstack")

LOG = logging.getLogger(__name__)


def get_admin_connection(
    jhelper: JujuHelper, deployment: Deployment, *, region_name: str | None = None
) -> "openstack.connection.Connection":
    """Return a connection to keystone using admin credentials.

    :param jhelper: Juju helpers for retrieving admin credentials
    :param region_name: Region to select from the service catalog
    :raises: openstack.exceptions.SDKException
    """
    admin_auth_info = retrieve_admin_credentials(jhelper, deployment, OPENSTACK_MODEL)
    options: dict[str, typing.Any] = {}
    if region_name is not None:
        options["region_name"] = region_name
    conn = openstack.connect(
        auth_url=admin_auth_info.get("OS_AUTH_URL"),
        username=admin_auth_info.get("OS_USERNAME"),
        password=admin_auth_info.get("OS_PASSWORD"),
        project_name=admin_auth_info.get("OS_PROJECT_NAME"),
        user_domain_name=admin_auth_info.get("OS_USER_DOMAIN_NAME"),
        project_domain_name=admin_auth_info.get("OS_PROJECT_DOMAIN_NAME"),
        # Pin service lookups to this deployment's region in multi-region clouds.
        region_name=admin_auth_info.get("OS_REGION_NAME"),
        cacert=admin_auth_info.get("OS_CACERT"),
        **options,
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


def resolve_hypervisor_name(
    node_name: str, conn: "openstack.connection.Connection"
) -> str:
    """Find Nova's name for a compute node before checking for its guests.

    The cluster and Nova may use different short or fully qualified names.
    Querying Nova with the wrong name can miss running guests, so require one match.
    """
    # Match the whole short host label, so cloud-1 cannot match cloud-11.maas.
    short_name = node_name.split(".", 1)[0]
    matches = [
        hypervisor.name
        for hypervisor in conn.compute.hypervisors()
        if hypervisor.name
        and (
            hypervisor.name == node_name
            or ("." not in node_name and hypervisor.name.startswith(f"{node_name}."))
            or ("." in node_name and hypervisor.name == short_name)
        )
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Cannot identify a unique Nova hypervisor for {node_name}: "
            f"found {matches}. Check the node hostname in Nova."
        )
    return matches[0]


def remove_compute_service(
    hypervisor_name: str, conn: "openstack.connection.Connection"
) -> None:
    """Remove compute services associated with hypervisor from nova.

    :param hypervisor_name: Name of hypervisor
    :param conn: Admin connection
    """
    for service in conn.compute.services(host=hypervisor_name):
        LOG.info("Disabling %s on %s", service.binary, service.host)
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
        LOG.info("Disabling %s on %s", service.binary, service.host)
        conn.network.delete_agent(service)


def remove_hypervisor(
    jhelper: JujuHelper, deployment: Deployment, hypervisor_name: str
) -> None:
    """Remove services associated with hypervisor from OpenStack.

    :param jhelper: Juju helpers for retrieving admin credentials
    :param deployment: Deployment to obtain region and model from
    :param hypervisor_name: Name of hypervisor
    """
    conn = get_admin_connection(jhelper, deployment)
    remove_compute_service(hypervisor_name, conn)
    remove_network_service(hypervisor_name, conn)
