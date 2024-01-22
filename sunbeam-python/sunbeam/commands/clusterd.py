# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import ipaddress
import logging
import re
from typing import List, Optional, Union

from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ClusterAlreadyBootstrappedException,
    ClusterServiceUnavailableException,
    ConfigItemNotFoundException,
    JujuUserNotFoundException,
    LastNodeRemovalFromClusterException,
    NodeAlreadyExistsException,
    NodeJoinException,
    NodeNotExistInClusterException,
    TokenAlreadyGeneratedException,
    TokenNotFoundException,
)
from sunbeam.commands.juju import BOOTSTRAP_CONFIG_KEY, JujuStepHelper
from sunbeam.jobs import questions
from sunbeam.jobs.common import BaseStep, Result, ResultType, Status
from sunbeam.jobs.juju import JujuController

CLUSTERD_PORT = 7000
LOG = logging.getLogger(__name__)


class ClusterInitStep(BaseStep):
    """Bootstrap clustering on sunbeam clusterd."""

    def __init__(self, client: Client, role: List[str]):
        super().__init__("Bootstrap Cluster", "Bootstrapping Sunbeam cluster")

        self.port = CLUSTERD_PORT
        self.role = role
        self.client = client
        self.fqdn = utils.get_fqdn()
        self.ip = utils.get_local_ip_by_default_route()

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            members = self.client.cluster.get_cluster_members()
            LOG.info(members)
            member_names = [member.get("name") for member in members]
            if self.fqdn in member_names:
                return Result(ResultType.SKIPPED)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            if "Sunbeam Cluster not initialized" in str(e):
                return Result(ResultType.COMPLETED)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Bootstrap sunbeam cluster"""
        try:
            self.client.cluster.bootstrap(
                name=self.fqdn, address=f"{self.ip}:{self.port}", role=self.role
            )
            return Result(ResultType.COMPLETED)
        except ClusterAlreadyBootstrappedException:
            LOG.debug("Cluster already bootstrapped")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            return Result(ResultType.FAILED, str(e))


class ClusterAddNodeStep(BaseStep):
    """Generate token for new node to join in cluster."""

    def __init__(self, client: Client, name: str):
        super().__init__(
            "Add Node Cluster",
            "Generating token for new node to join cluster",
        )

        self.node_name = name
        self.client = client

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            members = self.client.cluster.get_cluster_members()
            LOG.debug(members)
            member_names = [member.get("name") for member in members]
            if self.node_name in member_names:
                return Result(ResultType.SKIPPED)

            # If node is not cluster member, check if it the node has
            # already generated token
            tokens = self.client.cluster.list_tokens()
            token_d = {token.get("name"): token.get("token") for token in tokens}
            if self.node_name in token_d:
                return Result(ResultType.SKIPPED, token_d.get(self.node_name))
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Add node to sunbeam cluster"""
        try:
            token = self.client.cluster.add_node(name=self.node_name)
            LOG.info(token)
            return Result(result_type=ResultType.COMPLETED, message=token)
        except TokenAlreadyGeneratedException as e:
            LOG.warning(e)
            return Result(ResultType.FAILED, str(e))


class ClusterJoinNodeStep(BaseStep):
    """Join node to the sunbeam cluster."""

    def __init__(self, client: Client, token: str, role: List[str]):
        super().__init__("Join node to Cluster", "Adding node to Sunbeam cluster")

        self.port = CLUSTERD_PORT
        self.client = client
        self.token = token
        self.role = role
        self.fqdn = utils.get_fqdn()
        self.ip = utils.get_local_ip_by_default_route()

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            members = self.client.cluster.get_cluster_members()
            LOG.info(members)
            member_names = [member.get("name") for member in members]
            if self.fqdn in member_names:
                return Result(ResultType.SKIPPED)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            if "Sunbeam Cluster not initialized" in str(e):
                return Result(ResultType.COMPLETED)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Join node to sunbeam cluster"""
        try:
            self.client.cluster.join_node(
                name=self.fqdn,
                address=f"{self.ip}:{self.port}",
                token=self.token,
                role=self.role,
            )
            LOG.info(self.token)
            return Result(result_type=ResultType.COMPLETED, message=self.token)
        except (NodeAlreadyExistsException, NodeJoinException) as e:
            LOG.warning(e)
            return Result(ResultType.FAILED, str(e))


class ClusterListNodeStep(BaseStep):
    """List nodes in the sunbeam cluster."""

    def __init__(self, client: Client):
        super().__init__("List nodes of Cluster", "Listing nodes in Sunbeam cluster")
        self.client = client

    def run(self, status: Optional[Status] = None) -> Result:
        """List nodes in the sunbeam cluster"""
        try:
            members = self.client.cluster.get_cluster_members()
            LOG.debug(f"Members: {members}")
            nodes = self.client.cluster.list_nodes()
            LOG.debug(f"Nodes: {nodes}")

            nodes_dict = {
                member.get("name"): {"status": member.get("status")}
                for member in members
            }
            for node in nodes:
                nodes_dict[node.get("name")].update({"roles": node.get("role", [])})

            return Result(result_type=ResultType.COMPLETED, message=nodes_dict)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))


class ClusterUpdateNodeStep(BaseStep):
    """Update node info in the cluster database."""

    def __init__(
        self,
        client: Client,
        name: str,
        role: Optional[List[str]] = None,
        machine_id: int = -1,
    ):
        super().__init__("Update node info", "Updating node info in cluster database")
        self.client = client
        self.name = name
        self.role = role
        self.machine_id = machine_id

    def run(self, status: Optional[Status] = None) -> Result:
        """Update Node info"""
        try:
            self.client.cluster.update_node_info(self.name, self.role, self.machine_id)
            return Result(result_type=ResultType.COMPLETED)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))


class ClusterRemoveNodeStep(BaseStep):
    """Remove node from the sunbeam cluster."""

    def __init__(self, client: Client, name: str):
        super().__init__(
            "Remove node from Cluster", "Removing node from Sunbeam cluster"
        )
        self.node_name = name
        self.client = client

    def run(self, status: Optional[Status] = None) -> Result:
        """Remove node from sunbeam cluster"""
        try:
            self.client.cluster.remove_node(self.node_name)
            return Result(result_type=ResultType.COMPLETED)
        except (
            TokenNotFoundException,
            NodeNotExistInClusterException,
        ) as e:
            # Consider these exceptions as soft ones
            LOG.warning(e)
            return Result(ResultType.COMPLETED)
        except (LastNodeRemovalFromClusterException, Exception) as e:
            LOG.warning(e)
            return Result(ResultType.FAILED, str(e))


class ClusterAddJujuUserStep(BaseStep):
    """Add Juju user in cluster database."""

    def __init__(self, client: Client, name: str, token: str):
        super().__init__(
            "Add Juju user to cluster DB",
            "Adding Juju user to cluster database",
        )

        self.username = name
        self.token = token
        self.client = client

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            user = self.client.cluster.get_juju_user(self.username)
            LOG.debug(f"JujuUser {user} found in database.")
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))
        except JujuUserNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Add node to sunbeam cluster"""
        try:
            self.client.cluster.add_juju_user(self.username, self.token)
            return Result(result_type=ResultType.COMPLETED)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))


class ClusterUpdateJujuControllerStep(BaseStep, JujuStepHelper):
    """Save Juju controller in cluster database."""

    def __init__(self, client: Client, controller: str):
        super().__init__(
            "Add Juju controller to cluster DB",
            "Adding Juju controller to cluster database",
        )

        self.client = client
        self.controller = controller

    def _extract_ip(self, ip) -> Union[ipaddress.IPv4Address, ipaddress.IPv6Address]:
        """Extract ip from ipv4 or ipv6 ip:port"""
        # Check for ipv6 addr
        ipv6_addr = re.match(r"\[(.*?)\]", ip)
        if ipv6_addr:
            ip_str = ipv6_addr.group(1)
        else:
            ip_str = ip.split(":")[0]
        return ipaddress.ip_address(ip_str)

    def filter_ips(self, ips: List[str], network_str: Optional[str]) -> List[str]:
        """Filter ips missing from specified networks

        :param ips: list of ips to filter
        :param network_str: network to filter ips from, separated by comma
        """
        if network_str is None:
            return ips
        networks = [ipaddress.ip_network(network) for network in network_str.split(",")]
        return list(
            filter(
                lambda ip: any(
                    True for network in networks if self._extract_ip(ip) in network
                ),
                ips,
            )
        )

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            variables = questions.load_answers(self.client, BOOTSTRAP_CONFIG_KEY)
            self.networks = variables.get("bootstrap", {}).get("management_cidr")
            juju_controller = JujuController.load(self.client)
            LOG.debug(f"Controller(s) present at: {juju_controller.api_endpoints}")
            if not juju_controller.api_endpoints:
                LOG.debug(
                    "Controller endpoints are empty in database, so update the "
                    "database by getting controller endpoints again"
                )
                return Result(ResultType.COMPLETED)

            if juju_controller.api_endpoints == self.filter_ips(
                juju_controller.api_endpoints, self.networks
            ):
                # Controller found, and parsed successfully
                return Result(ResultType.SKIPPED)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))
        except ConfigItemNotFoundException:
            pass  # Credentials missing, schedule for record
        except TypeError as e:
            # Note(gboutry): Credentials invalid, schedule for record
            LOG.warning(e)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Save controller in sunbeam cluster."""
        controller = self.get_controller(self.controller)["details"]

        juju_controller = JujuController(
            api_endpoints=self.filter_ips(controller["api-endpoints"], self.networks),
            ca_cert=controller["ca-cert"],
        )
        try:
            juju_controller.write(self.client)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))

        return Result(result_type=ResultType.COMPLETED)
