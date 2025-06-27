# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import logging
import re

from rich.console import Console

import sunbeam.versions as versions
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
    URLNotFoundException,
)
from sunbeam.core import questions
from sunbeam.core.common import BaseStep, Result, ResultType, Status
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuController,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
    ModelNotFoundException,
)
from sunbeam.core.manifest import CharmManifest, Manifest
from sunbeam.steps.juju import BOOTSTRAP_CONFIG_KEY

LOG = logging.getLogger(__name__)
APPLICATION = "sunbeam-clusterd"
SUNBEAM_CLUSTERD_APP_TIMEOUT = (
    1200  # 20 minutes, adding / removing units can take a long time
)
CLUSTERD_PORT = 7000


def bootstrap_questions():
    return {
        "management_cidr": questions.PromptQuestion(
            "Management network",
            default_value=utils.get_local_cidr_by_default_route(),
            description=(
                "Management network should be available on every node of"
                " the deployment. It is used for communication between"
                " the nodes of the deployment. Requires CIDR format, can "
                "be a comma-separated list."
            ),
        ),
    }


class ClusterInitStep(BaseStep):
    """Bootstrap clustering on sunbeam clusterd."""

    def __init__(
        self, client: Client, role: list[str], machineid: int, management_cidr: str
    ):
        super().__init__("Bootstrap Cluster", "Bootstrapping Sunbeam cluster")

        self.port = CLUSTERD_PORT
        self.role = role
        self.machineid = machineid
        self.client = client
        self.management_cidr = management_cidr

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.fqdn = utils.get_fqdn(self.management_cidr)
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

    def run(self, status: Status | None = None) -> Result:
        """Bootstrap sunbeam cluster."""
        try:
            ip = utils.get_local_ip_by_cidr(self.management_cidr)
        except ValueError as e:
            LOG.debug("Failed to determine host IP address", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        address = f"{ip}:{self.port}"
        try:
            self.client.cluster.bootstrap(
                name=self.fqdn,
                address=address,
                role=self.role,
                machineid=self.machineid,
            )
            LOG.debug("Bootstrapped clusterd on %s", address)
            return Result(ResultType.COMPLETED)
        except ClusterAlreadyBootstrappedException:
            LOG.debug("Cluster already bootstrapped")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            return Result(ResultType.FAILED, str(e))


class AskManagementCidrStep(BaseStep):
    """Determine the management CIDR."""

    _CONFIG = BOOTSTRAP_CONFIG_KEY

    def __init__(
        self,
        client: Client,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Management CIDR", "Determining management CIDR")
        self.client = client
        self.manifest = manifest
        self.accept_defaults = accept_defaults
        self.variables: dict = {}

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        try:
            self.variables = questions.load_answers(self.client, self._CONFIG)
            if self.variables.get("bootstrap", {}).get("management_cidr"):
                # This step will always be called before the clusterd bootstrap
                # And it *might* be called afterwards
                # We don't allow updating it
                return
        except (ClusterServiceUnavailableException, URLNotFoundException):
            self.variables = {}
        self.variables.setdefault("bootstrap", {})
        preseed = {}
        if self.manifest and (bootstrap := self.manifest.core.config.bootstrap):
            preseed = bootstrap.model_dump(by_alias=True)

        bootstrap_bank = questions.QuestionBank(
            questions=bootstrap_questions(),
            console=console,  # type: ignore
            preseed=preseed,
            previous_answers=self.variables.get("bootstrap", {}),
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )

        self.variables["bootstrap"]["management_cidr"] = (
            bootstrap_bank.management_cidr.ask()
        )

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def run(self, status: Status | None = None) -> Result:
        """Determine the management CIDR."""
        return Result(
            ResultType.COMPLETED, self.variables["bootstrap"]["management_cidr"]
        )


class SaveManagementCidrStep(BaseStep):
    """Save the management CIDR in clusterd."""

    _CONFIG = BOOTSTRAP_CONFIG_KEY

    def __init__(self, client: Client, management_cidr: str):
        super().__init__("Save Management CIDR", "Saving management CIDR in clusterd")

        self.client = client
        self.management_cidr = management_cidr

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.variables = questions.load_answers(self.client, self._CONFIG)
        except ClusterServiceUnavailableException as e:
            LOG.debug("Failed to load management cidr from clusterd", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        current_cidr = self.variables.get("bootstrap", {}).get("management_cidr")
        if current_cidr == self.management_cidr:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Save the management CIDR in clusterd."""
        bootstrap = self.variables.get("bootstrap", {})
        bootstrap["management_cidr"] = self.management_cidr
        self.variables["bootstrap"] = bootstrap
        try:
            questions.write_answers(self.client, self._CONFIG, self.variables)
        except ClusterServiceUnavailableException as e:
            LOG.debug("Failed to save management cidr in clusterd", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class ClusterAddNodeStep(BaseStep):
    """Generate token for new node to join in cluster."""

    def __init__(self, client: Client, name: str):
        super().__init__(
            "Add Node Cluster",
            "Generating token for new node to join cluster",
        )

        self.node_name = name
        self.client = client

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
        """Add node to sunbeam cluster."""
        try:
            token = self.client.cluster.add_node(name=self.node_name)
            LOG.info(token)
            return Result(result_type=ResultType.COMPLETED, message=token)
        except TokenAlreadyGeneratedException as e:
            LOG.warning(e)
            return Result(ResultType.FAILED, str(e))


class ClusterJoinNodeStep(BaseStep):
    """Join node to the sunbeam cluster."""

    def __init__(
        self,
        client: Client,
        token: str,
        host_address: str,
        fqdn: str,
        role: list[str],
    ):
        super().__init__("Join node to Cluster", "Adding node to Sunbeam cluster")

        self.port = CLUSTERD_PORT
        self.client = client
        self.token = token
        self.role = role
        self.ip = host_address
        self.fqdn = fqdn

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
        """Join node to sunbeam cluster."""
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

    def run(self, status: Status | None = None) -> Result:
        """List nodes in the sunbeam cluster."""
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
        role: list[str] | None = None,
        machine_id: int = -1,
    ):
        super().__init__("Update node info", "Updating node info in cluster database")
        self.client = client
        self.node_name = name
        self.role = role
        self.machine_id = machine_id

    def run(self, status: Status | None = None) -> Result:
        """Update Node info."""
        try:
            self.client.cluster.update_node_info(
                self.node_name, self.role, self.machine_id
            )
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

    def run(self, status: Status | None = None) -> Result:
        """Remove node from sunbeam cluster."""
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

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
        """Add node to sunbeam cluster."""
        try:
            self.client.cluster.add_juju_user(self.username, self.token)
            return Result(result_type=ResultType.COMPLETED)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))


class ClusterUpdateJujuControllerStep(BaseStep, JujuStepHelper):
    """Save Juju controller in cluster database.

    The controller IP is filtered based on the management_cidr
    saved in the cluster database by default.
    If filter_endpoints is False, the controller IP is not
    filtered and all the controller IPs are saved in cluster
    database.
    """

    def __init__(
        self,
        client: Client,
        controller: str,
        is_external: bool = False,
        filter_endpoints: bool = True,
    ):
        super().__init__(
            "Add Juju controller to cluster DB",
            "Adding Juju controller to cluster database",
        )

        self.client = client
        self.controller = controller
        self.is_external = is_external
        self.filter_endpoints = filter_endpoints

    def _extract_ip(self, ip) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        """Extract ip from ipv4 or ipv6 ip:port."""
        # Check for ipv6 addr
        ipv6_addr = re.match(r"\[(.*?)\]", ip)
        if ipv6_addr:
            ip_str = ipv6_addr.group(1)
        else:
            ip_str = ip.split(":")[0]
        return ipaddress.ip_address(ip_str)

    def filter_ips(self, ips: list[str], network_str: str | None) -> list[str]:
        """Filter ips missing from specified networks.

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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.controller_details = self.get_controller(self.controller)["details"]

        try:
            variables = questions.load_answers(self.client, BOOTSTRAP_CONFIG_KEY)
            self.networks = variables.get("bootstrap", {}).get("management_cidr")

            # Do not filter api endpoints based on management cidr
            if not self.filter_endpoints:
                self.networks = None

            juju_controller = JujuController.load(self.client)
            LOG.debug(f"Controller(s) present at: {juju_controller.api_endpoints}")
            if not juju_controller.api_endpoints:
                LOG.debug(
                    "Controller endpoints are empty in database, so update the "
                    "database by getting controller endpoints again"
                )
                return Result(ResultType.COMPLETED)

            if juju_controller.api_endpoints == self.filter_ips(
                self.controller_details["api-endpoints"], self.networks
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

    def run(self, status: Status | None = None) -> Result:
        """Save controller in sunbeam cluster."""
        juju_controller = JujuController(
            name=self.controller,
            api_endpoints=self.filter_ips(
                self.controller_details["api-endpoints"], self.networks
            ),
            ca_cert=self.controller_details["ca-cert"],
            is_external=self.is_external,
        )
        try:
            juju_controller.write(self.client)
        except ClusterServiceUnavailableException as e:
            LOG.debug(e)
            return Result(ResultType.FAILED, str(e))

        return Result(result_type=ResultType.COMPLETED)


class DeploySunbeamClusterdApplicationStep(BaseStep):
    """Deploy sunbeam-clusterd application."""

    def __init__(
        self,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            "Deploy sunbeam-clusterd",
            "Deploying Sunbeam Clusterd",
        )
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model
        self.app = APPLICATION

    def is_skip(self, status: Status | None = None) -> Result:
        """Check wheter or not to deploy sunbeam-clusterd."""
        try:
            self.jhelper.get_application(self.app, self.model)
        except ModelNotFoundException:
            return Result(ResultType.FAILED, f"Model {self.model} not found")
        except ApplicationNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Deploy sunbeam clusterd to infra machines."""
        self.update_status(status, "fetching infra machines")
        infra_machines = self.jhelper.get_machines(self.model)
        machines = list(infra_machines.keys())

        self.update_status(status, "computing number of units for sunbeam-clusterd")
        num_machines = len(machines)
        if num_machines == 0:
            return Result(ResultType.FAILED, f"No machines found in {self.model} model")

        num_units = num_machines
        self.update_status(status, "deploying application")
        charm_manifest: CharmManifest = self.manifest.core.software.charms[
            "sunbeam-clusterd"
        ]
        charm_config = {"snap-channel": versions.SNAP_SUNBEAM_CLUSTERD_CHANNEL}
        if charm_manifest.config:
            charm_config.update(charm_manifest.config)
        self.jhelper.deploy(
            APPLICATION,
            "sunbeam-clusterd",
            self.model,
            num_units,
            channel=charm_manifest.channel,
            revision=charm_manifest.revision,
            to=machines,
            config=charm_config,
            base=versions.JUJU_BASE,
        )

        apps = self.jhelper.get_application_names(self.model)
        try:
            self.jhelper.wait_until_active(
                self.model,
                apps,
                timeout=SUNBEAM_CLUSTERD_APP_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
