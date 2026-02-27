# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import enum
from collections.abc import Iterable

import pydantic

from sunbeam.clusterd.client import Client
from sunbeam.core.common import Role
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.questions import load_answers, write_answers

CLUSTERD_CONFIG_KEY = "OvnConfig"
SNAP_PROVIDER_CONFIG_KEY = "feature.microovn-sdn"


class OvnProvider(enum.StrEnum):
    OVN_K8S = "ovn-k8s"
    MICROOVN = "microovn"


class OvnConfig(pydantic.BaseModel):
    provider: OvnProvider | None = None


DEFAULT_PROVIDER = OvnProvider.OVN_K8S


def load_provider_config(client: Client) -> OvnConfig:
    """Load the OVN provider configuration from the cluster deployment answers.

    :param client: the Sunbeam client
    :return: the OVN provider configuration
    """
    answers = load_answers(client, CLUSTERD_CONFIG_KEY)
    return OvnConfig.model_validate(answers)


def write_provider_config(client: Client, config: OvnConfig) -> None:
    """Write the OVN provider configuration to the cluster deployment answers.

    :param client: the Sunbeam client
    :param config: the OVN provider configuration
    """
    write_answers(client, CLUSTERD_CONFIG_KEY, config.model_dump())


class OvnManager:
    def __init__(self, client: Client):
        self.client = client

    def get_provider(self) -> OvnProvider:
        """Get the OVN provider from the configuration."""
        config = load_provider_config(self.client)
        if config.provider is None:
            return DEFAULT_PROVIDER
        return config.provider

    def get_roles_for_microovn(self) -> set[Role]:
        """Get list of roles where microovn is necessary.

        :return: set of roles
        """
        provider = self.get_provider()
        roles = {Role.NETWORK}
        if provider == OvnProvider.MICROOVN:
            roles |= {Role.COMPUTE, Role.CONTROL}
        return roles

    def is_microovn_necessary(self, roles: Iterable[Role]) -> bool:
        """Check if microovn is necessary for the given roles.

        :param roles: iterable of roles
        :return: True if microovn is necessary, False otherwise
        """
        return len(self.get_roles_for_microovn().intersection(roles)) > 0

    def is_network_agent_dataplane_node(self, roles: Iterable[Role]) -> bool:
        """Check whether the node is a network agent dataplane node.

        :param roles: iterable of roles
        :return: True if the role is managed by openstack-network-agents,
            False otherwise
        """
        provider = self.get_provider()
        dataplane_roles = {Role.NETWORK}
        if provider == OvnProvider.MICROOVN:
            dataplane_roles.add(Role.COMPUTE)
        return len(dataplane_roles.intersection(roles)) > 0

    def is_microovn_necessary_maas(
        self, nb_network: int, nb_compute: int, nb_control: int
    ) -> bool:
        """Check if microovn is necessary for the given number of roles in MAAS.

        :param nb_network: number of network nodes
        :param nb_compute: number of compute nodes
        :param nb_control: number of control nodes
        :return: True if microovn is necessary, False otherwise
        """
        provider = self.get_provider()
        if provider == OvnProvider.MICROOVN:
            return (nb_network + nb_compute + nb_control) > 0
        else:
            return nb_network > 0

    def get_machines(self) -> list[str]:
        """Get the list of machine IDs for the OVN provider.

        :return: list of machine IDs as strings
        """
        nodes = self.client.cluster.list_nodes_by_role("network")
        if self.get_provider() == OvnProvider.MICROOVN:
            nodes += self.client.cluster.list_nodes_by_role("compute")
            nodes += self.client.cluster.list_nodes_by_role("control")
        machine_ids = {
            str(node.get("machineid"))
            for node in nodes
            if node.get("machineid") not in (-1, None)
        }
        return sorted(machine_ids)

    def get_control_plane_tfvars(
        self, deployment: Deployment, jhelper: JujuHelper
    ) -> dict:
        """Get the Terraform variables for the OVN control plane.

        :return: dict of Terraform variables
        """
        provider = self.get_provider()
        tfvars = {}
        if provider == OvnProvider.MICROOVN:
            model_name = jhelper.get_model_name_with_owner(
                deployment.openstack_machines_model
            )
            tfvars["external-ovsdb-cms-offer-url"] = model_name + ".sunbeam-ovn-proxy"
        return tfvars
