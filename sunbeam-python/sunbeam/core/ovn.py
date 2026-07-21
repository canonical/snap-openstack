# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Iterable

from sunbeam.clusterd.client import Client
from sunbeam.core.common import Role
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper

DEFAULT_ARCHITECTURE = "amd64"
ARM64_ARCHITECTURE = "arm64"


class OvnManager:
    def __init__(self, client: Client):
        self.client = client

    def get_roles_for_microovn(self) -> set[Role]:
        """Get list of roles where microovn is necessary.

        :return: set of roles
        """
        return {Role.CONTROL, Role.COMPUTE, Role.NETWORK}

    def is_microovn_necessary(self, roles: Iterable[Role]) -> bool:
        """Check if microovn is necessary for the given roles.

        :param roles: iterable of roles
        :return: True if microovn is necessary, False otherwise
        """
        return len(self.get_roles_for_microovn().intersection(roles)) > 0

    def is_microovn_necessary_maas(
        self, nb_network: int, nb_compute: int, nb_control: int
    ) -> bool:
        """Check if microovn is necessary for the given number of roles in MAAS.

        :param nb_network: number of network nodes
        :param nb_compute: number of compute nodes
        :param nb_control: number of control nodes
        :return: True if microovn is necessary, False otherwise
        """
        return (nb_network + nb_compute + nb_control) > 0

    def _list_microovn_nodes(self) -> list[dict]:
        """Collect cluster nodes that should run MicroOVN."""
        return (
            self.client.cluster.list_nodes_by_role("control")
            + self.client.cluster.list_nodes_by_role("compute")
            + self.client.cluster.list_nodes_by_role("network")
        )

    def get_token_distributor_machines(self) -> list[str]:
        """Get machine IDs for MicroOVN helper applications."""
        for role in (Role.CONTROL, Role.COMPUTE, Role.NETWORK):
            machine_ids: set[str] = set()
            for node in self.client.cluster.list_nodes_by_role(role.name.lower()):
                machineid = node.get("machineid")
                if machineid in (-1, None):
                    continue
                arch = node.get("arch") or DEFAULT_ARCHITECTURE
                if arch == DEFAULT_ARCHITECTURE:
                    machine_ids.add(str(machineid))
            if machine_ids:
                return sorted(machine_ids)

        return []

    def get_machines(self, architecture: str | None = None) -> list[str]:
        """Get machine IDs for MicroOVN, optionally filtered by architecture.

        :param architecture: if set, only return machines with this arch
        :return: list of machine IDs as strings
        """
        machine_ids: set[str] = set()
        for node in self._list_microovn_nodes():
            machineid = node.get("machineid")
            if machineid in (-1, None):
                continue
            arch = node.get("arch") or DEFAULT_ARCHITECTURE
            if architecture is None or arch == architecture:
                machine_ids.add(str(machineid))
        return sorted(machine_ids)

    def get_machines_by_architecture(self) -> dict[str, list[str]]:
        """Get MicroOVN machine IDs grouped by architecture."""
        machine_ids_by_arch: dict[str, set[str]] = {}
        for node in self._list_microovn_nodes():
            machineid = node.get("machineid")
            if machineid in (-1, None):
                continue
            arch = node.get("arch") or DEFAULT_ARCHITECTURE
            machine_ids_by_arch.setdefault(arch, set()).add(str(machineid))
        return {
            arch: sorted(machine_ids)
            for arch, machine_ids in machine_ids_by_arch.items()
        }

    def get_control_plane_tfvars(
        self, deployment: Deployment, jhelper: JujuHelper
    ) -> dict:
        """Get the Terraform variables for the OVN control plane.

        :return: dict of Terraform variables
        """
        model_name = jhelper.get_model_name_with_owner(
            deployment.openstack_machines_model
        )
        return {"external-ovsdb-cms-offer-url": model_name + ".sunbeam-ovn-proxy"}
