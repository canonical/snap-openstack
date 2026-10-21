# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Resolve a node name given on the CLI to the names used by each subsystem."""

import functools
import typing

import click

from sunbeam.core.k8s import HOSTNAME_LABEL, list_nodes
from sunbeam.core.openstack_api import get_admin_connection
from sunbeam.steps.k8s import get_kube_client
from sunbeam.utils import hostnames_match, match_hostname

if typing.TYPE_CHECKING:
    from sunbeam.core.deployment import Deployment
    from sunbeam.core.juju import JujuHelper


class ResolvedNode:
    """Names a node is known as by juju, clusterd, nova and k8s.

    The name shown by `sunbeam cluster list` (juju machine hostname), the
    clusterd node name, the nova hypervisor hostname and the k8s node name
    can all differ for the same machine (short hostname vs FQDN). Each
    property resolves the node name given on the CLI against the subsystem
    that uses it.
    """

    def __init__(
        self,
        name: str,
        deployment: "Deployment",
        cluster_status: dict[str, typing.Any],
        jhelper: "JujuHelper",
    ):
        self.name = name
        self.deployment = deployment
        self.cluster_status = cluster_status
        self.jhelper = jhelper

    @functools.cached_property
    def hostname(self) -> str:
        """Juju machine hostname, as shown in `sunbeam cluster list`."""
        match = match_hostname(self.name, self.cluster_status)
        if match is not None:
            return match
        raise click.ClickException(f"'{self.name}' does not exist in cluster.")

    @functools.cached_property
    def machine_id(self) -> str:
        """Juju machine id of the node."""
        machines = self.jhelper.get_machines(self.deployment.openstack_machines_model)
        for machine_id, machine in machines.items():
            if machine.hostname == self.hostname or machine.dns_name == self.name:
                return machine_id
        matches = {
            machine_id
            for machine_id, machine in machines.items()
            if hostnames_match(self.hostname, machine.hostname or "")
            or hostnames_match(self.name, machine.dns_name or "")
        }
        if len(matches) == 1:
            return matches.pop()
        raise click.ClickException(f"Juju machine not found for node '{self.name}'.")

    @functools.cached_property
    def clusterd_name(self) -> str:
        """Clusterd node name."""
        nodes = self.deployment.get_client().cluster.list_nodes()
        for node in nodes:
            if str(node.get("machineid")) == self.machine_id:
                return typing.cast(str, node["name"])
        match = match_hostname(self.name, (typing.cast(str, n["name"]) for n in nodes))
        if match is not None:
            return match
        raise click.ClickException(f"'{self.name}' does not exist in cluster.")

    @functools.cached_property
    def hypervisor_name(self) -> str:
        """Nova hypervisor hostname."""
        conn = get_admin_connection(self.jhelper, self.deployment)
        match = match_hostname(
            self.hostname,
            (
                hypervisor.name
                for hypervisor in conn.compute.hypervisors()
                if hypervisor.name
            ),
        )
        if match is not None:
            return match
        raise click.ClickException(f"No nova hypervisor found for node '{self.name}'.")

    @functools.cached_property
    def k8s_name(self) -> str:
        """K8s node name."""
        kube_client = get_kube_client(self.deployment.get_client())
        # The sunbeam/hostname label maps the clusterd node name to the k8s
        # node name (set by EnsureK8SUnitsTaggedStep); fall back to a
        # short-hostname match for nodes labelled before that step existed.
        nodes = list_nodes(kube_client, labels={HOSTNAME_LABEL: self.clusterd_name})
        if len(nodes) == 1 and nodes[0].metadata and nodes[0].metadata.name:
            return nodes[0].metadata.name
        match = match_hostname(
            self.hostname,
            (
                node.metadata.name
                for node in list_nodes(kube_client, labels={})
                if node.metadata and node.metadata.name
            ),
        )
        if match is not None:
            return match
        raise click.ClickException(f"No k8s node found for node '{self.name}'.")
