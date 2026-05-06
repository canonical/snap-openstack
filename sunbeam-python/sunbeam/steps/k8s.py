# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import ipaddress
import json
import logging
import subprocess
import time
import typing

import jubilant
import tenacity
import yaml
from rich.console import Console

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
    Status,
    StepContext,
    SunbeamException,
    read_config,
    update_config,
    validate_cidr_or_ip_ranges,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
    JujuStepHelper,
    LeaderNotFoundException,
    MachineNotFoundException,
    ModelNotFoundException,
    UnsupportedKubeconfigException,
)
from sunbeam.core.k8s import (
    CREDENTIAL_SUFFIX,
    DEPLOYMENT_LABEL,
    HOSTNAME_LABEL,
    K8S_CLOUD_SUFFIX,
    K8S_KUBECONFIG_KEY,
    LOADBALANCER_QUESTION_DESCRIPTION,
    K8SError,
    K8SHelper,
    K8SNodeNotFoundError,
    cordon,
    drain,
    fetch_pods,
    fetch_pods_for_eviction,
    find_node,
    list_nodes,
    uncordon,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.questions import (
    PromptQuestion,
    QuestionBank,
    load_answers,
    write_answers,
)
from sunbeam.core.steps import (
    DeployMachineApplicationStep,
    DestroyMachineApplicationStep,
    RemoveMachineUnitsStep,
)
from sunbeam.core.terraform import TerraformHelper
from sunbeam.lazy import LazyImport

if typing.TYPE_CHECKING:
    import lightkube.config.kubeconfig as l_kubeconfig
    import lightkube.core.client as l_client
    import lightkube.core.exceptions as l_exceptions
    import lightkube.types as l_patch_type
    from lightkube.models import meta_v1
    from lightkube.resources import apps_v1, autoscaling_v2, core_v1
else:
    l_kubeconfig = LazyImport("lightkube.config.kubeconfig")
    l_client = LazyImport("lightkube.core.client")
    l_exceptions = LazyImport("lightkube.core.exceptions")
    l_patch_type = LazyImport("lightkube.types")
    meta_v1 = LazyImport("lightkube.models.meta_v1")
    apps_v1 = LazyImport("lightkube.resources.apps_v1")
    autoscaling_v2 = LazyImport("lightkube.resources.autoscaling_v2")
    core_v1 = LazyImport("lightkube.resources.core_v1")


LOG = logging.getLogger(__name__)
K8S_CONFIG_KEY = "TerraformVarsK8S"
K8S_ADDONS_CONFIG_KEY = "TerraformVarsK8SAddons"
APPLICATION = "k8s"
K8S_APP_TIMEOUT = 1800  # 30 minutes, step includes adding / removing units
K8S_DESTROY_TIMEOUT = 900
K8S_UNIT_TIMEOUT = 1800  # 30 minutes, adding / removing units can take a long time
K8S_ENABLE_ADDONS_TIMEOUT = 300  # 5 minutes
K8SD_SNAP_SOCKET = "/var/snap/k8s/common/var/lib/k8sd/state/control.socket"
# Toleration seconds for node failure recovery k8s default of 5 min to 1 min
DEFAULT_NOT_READY_TOLERATION_SECONDS = 60
DEFAULT_UNREACHABLE_TOLERATION_SECONDS = 60

COREDNS_HPA = {
    "enabled": True,
    "minReplicas": 1,
    "maxReplicas": 100,
    "metrics": [
        {
            "type": "Resource",
            "resource": {
                "name": "cpu",
                "target": {"type": "Utilization", "averageUtilization": 80},
            },
        },
        {
            "type": "Resource",
            "resource": {
                "name": "memory",
                "target": {"type": "Utilization", "averageUtilization": 70},
            },
        },
    ],
}

COREDNS_PDB = {
    "minAvailable": 1,
}

COREDNS_RESOURCES = {
    "limits": {"cpu": "500m", "memory": "128Mi"},
    "requests": {"cpu": "500m", "memory": "128Mi"},
}


def validate_cidrs(ip_ranges: str, separator: str = ","):
    for ip_cidr in ip_ranges.split(separator):
        ipaddress.ip_network(ip_cidr)


def k8s_addons_questions():
    return {
        "loadbalancer": PromptQuestion(
            "OpenStack APIs IP ranges",
            default_value="172.16.1.201-172.16.1.240",
            validation_function=validate_cidr_or_ip_ranges,
            description=LOADBALANCER_QUESTION_DESCRIPTION,
        ),
    }


class KubeClientError(K8SError):
    pass


def get_kube_client(client: Client, namespace: str | None = None) -> "l_client.Client":
    try:
        kubeconfig_raw = read_config(client, K8SHelper.get_kubeconfig_key())
    except ConfigItemNotFoundException as e:
        LOG.debug("K8S kubeconfig not found")
        raise KubeClientError("K8S kubeconfig not found") from e

    kubeconfig = l_kubeconfig.KubeConfig.from_dict(kubeconfig_raw)
    try:
        return l_client.Client(
            kubeconfig,
            namespace,  # type: ignore
            trust_env=False,
        )
    except l_exceptions.ConfigError as e:
        LOG.debug("Error creating k8s client")
        raise KubeClientError("Error creating k8s client") from e


def load_addons_config(client: Client) -> dict:
    """Load K8S addons configuration."""
    return read_config(client, K8S_ADDONS_CONFIG_KEY)


def get_loadbalancer_config(client: Client) -> str | None:
    """Get the load balancer configuration."""
    addons_config = load_addons_config(client)
    return addons_config.get("k8s-addons", {}).get("loadbalancer")


class DeployK8SApplicationStep(DeployMachineApplicationStep):
    """Deploy K8S application using Terraform."""

    _ADDONS_CONFIG = K8S_ADDONS_CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        accept_defaults: bool = False,
        refresh: bool = False,
    ):
        super().__init__(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            K8S_CONFIG_KEY,
            APPLICATION,
            model,
            [Role.CONTROL, Role.REGION_CONTROLLER],
            "Deploy K8S",
            "Deploying K8S",
        )

        self.accept_defaults = accept_defaults
        self.refresh = refresh
        self.variables: dict = {}
        self.ranges: str | None = None
        self.traefik_variables: dict = {}

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
        self.variables = load_answers(self.client, self._ADDONS_CONFIG)
        self.variables.setdefault("k8s-addons", {})

        preseed = {}
        if k8s_addons := self.manifest.core.config.k8s_addons:
            preseed = k8s_addons.model_dump(by_alias=True)

        k8s_addons_bank = QuestionBank(
            questions=k8s_addons_questions(),
            console=console,  # type: ignore
            preseed=preseed,
            previous_answers=self.variables.get("k8s-addons", {}),
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )
        self.variables["k8s-addons"]["loadbalancer"] = (
            k8s_addons_bank.loadbalancer.ask()
        )
        write_answers(self.client, self._ADDONS_CONFIG, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        # No need to prompt for questions in case of refresh
        if self.refresh:
            return False

        return True

    def get_application_timeout(self) -> int:
        """Return application timeout."""
        return K8S_APP_TIMEOUT

    def _get_loadbalancer_range(self) -> str | None:
        """Return loadbalancer range stored in cluster db."""
        variables = load_answers(self.client, self._ADDONS_CONFIG)
        return variables.get("k8s-addons", {}).get("loadbalancer")

    def _get_k8s_config_tfvars(self) -> dict:
        config_tfvars: dict[str, bool | str | None] = {
            "load-balancer-enabled": True,
            "load-balancer-l2-mode": True,
        }

        charm_manifest = self.manifest.core.software.charms.get("k8s")
        if charm_manifest and charm_manifest.config:
            config_tfvars.update(charm_manifest.config)

        lb_range = self._get_loadbalancer_range()
        if lb_range:
            config_tfvars["load-balancer-cidrs"] = lb_range

        node_labels = str(config_tfvars.get("node-labels", "")).split(" ")
        node_labels.append("=".join((DEPLOYMENT_LABEL, self.deployment.name)))
        config_tfvars["node-labels"] = " ".join(
            node_label for node_label in node_labels if node_label
        )
        toleration_settings = {
            "default-not-ready-toleration-seconds": str(
                DEFAULT_NOT_READY_TOLERATION_SECONDS
            ),
            "default-unreachable-toleration-seconds": str(
                DEFAULT_UNREACHABLE_TOLERATION_SECONDS
            ),
        }
        existing_apiserver_args = [
            arg
            for arg in str(config_tfvars.get("kube-apiserver-extra-args", "")).split()
            if arg.split("=")[0] not in toleration_settings
        ]
        for key, value in toleration_settings.items():
            existing_apiserver_args.append(f"{key}={value}")
        config_tfvars["kube-apiserver-extra-args"] = " ".join(existing_apiserver_args)

        return config_tfvars

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        tfvars = {
            "endpoint_bindings": [
                {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                {
                    "endpoint": "cluster",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
            ],
            "k8s_config": self._get_k8s_config_tfvars(),
        }
        return tfvars


def _get_machines_space_ips(
    interfaces: dict[str, "jubilant.statustypes.NetworkInterface"],
    space: str,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> list[str]:
    ips = []
    for iface in interfaces.values():
        if (spaces := iface.space) and space in spaces.split():
            for ip in iface.ip_addresses:
                try:
                    ip_parsed = ipaddress.ip_address(ip)
                except ValueError:
                    LOG.debug("Invalid IP address %s", ip)
                    continue
                for network in networks:
                    if ip_parsed in network:
                        ips.append(ip)
    return ips


class EnsureK8SUnitsTaggedStep(BaseStep):
    """Ensure K8S units get properly tagged.

    This step ensures that every k8s node is tagged with the
    HOSTNAME_LABEL, to ensure sunbeam can query the correct nodes
    afterwards.
    Match is done on the IP addresses from the space configured as
    Networks.INTERNAL. Node IP in k8s is guaranteed by the cluster
    space binding.
    """

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        fqdn: str | None = None,
    ):
        super().__init__(
            "Ensure K8S units tagged", "Ensuring K8S units are properly tagged"
        )
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.fqdn = fqdn
        self.to_update: dict[str, str] = {}

    def _get_cluster_ips(
        self, juju_machine: "jubilant.statustypes.MachineStatus"
    ) -> list[str]:
        cluster_space = self.deployment.get_space(Networks.INTERNAL)
        cluster_networks = self.jhelper.get_space_networks(self.model, cluster_space)

        return _get_machines_space_ips(
            juju_machine.network_interfaces,
            cluster_space,
            cluster_networks,
        )

    @tenacity.retry(
        wait=tenacity.wait_fixed(30),
        stop=tenacity.stop_after_delay(600),
        retry=tenacity.retry_if_exception_type(ValueError),
        reraise=True,
    )
    def _find_matching_k8s_node(
        self,
        hostname: str,
        ips: list[str],
    ) -> "core_v1.Node":
        """Return matching k8s node.

        Match k8s node with the hostname and IP.
        Raises ValueError if no match for k8s node.

        Raises K8SError if client not able to get nodes
        from k8s.
        """
        LOG.debug(f"Matching K8S Node with name {hostname} and IPs {ips}")
        hostname_without_domain = hostname.split(".")[0]
        k8s_nodes = list_nodes(
            self.kube, labels={DEPLOYMENT_LABEL: self.deployment.name}
        )
        LOG.debug(f"K8S nodes filtered by deployment label: {k8s_nodes}")

        for k8s_node in k8s_nodes:
            if k8s_node.metadata is None:
                LOG.debug("K8S node has no metadata, %s", k8s_node)
                continue

            # Check for hostname with and without fqdn
            if k8s_node.metadata.name in [hostname, hostname_without_domain]:
                return k8s_node

            # Label should be always what is present in sunbeamd, so just
            # use hostname to filter
            if (
                k8s_node.metadata.labels
                and k8s_node.metadata.labels.get(HOSTNAME_LABEL) == hostname
            ):
                return k8s_node

            if k8s_node.status is None or k8s_node.status.addresses is None:
                LOG.debug("K8S node has no status nor addresses, %s", k8s_node)
                continue

            for ip in ips:
                for address in k8s_node.status.addresses:
                    if address.type == "InternalIP":
                        if address.address == ip:
                            return k8s_node
                    if address.type == "Hostname":
                        if address.address in [hostname, hostname_without_domain]:
                            return k8s_node

        raise ValueError("No K8s node matched")

    def _get_k8s_node_to_update(
        self,
        nodes: list[dict],
        machines: dict[str, "jubilant.statustypes.MachineStatus"],
    ) -> dict[str, str]:
        to_update = {}
        for node in nodes:
            sunbeam_name = node["name"]
            machine_id = str(node["machineid"])
            juju_machine = machines.get(machine_id)
            if juju_machine is None:
                LOG.debug("Machine %s not found in Juju", machine_id)
                raise SunbeamException(
                    f"{sunbeam_name!r} not found in Juju, expected id {machine_id!r}"
                )
            cluster_ips = self._get_cluster_ips(juju_machine)
            if not cluster_ips:
                LOG.debug("No cluster IPs found for machine %s", machine_id)
                raise SunbeamException(f"{sunbeam_name!r} has no cluster IPs")

            try:
                k8s_node = self._find_matching_k8s_node(sunbeam_name, cluster_ips)
            except ValueError:
                LOG.debug(
                    "No matching k8s node found for %s, cluster IPs %s",
                    sunbeam_name,
                    cluster_ips,
                )
                raise SunbeamException(f"{sunbeam_name} has no matching k8s node")
            except K8SError as e:
                LOG.debug("Failed to fetch k8s nodes", exc_info=True)
                raise SunbeamException("Failed to list nodes from K8S") from e

            if not k8s_node.metadata:
                LOG.debug("K8S node %s has no metadata", sunbeam_name)
                continue
            if not (
                k8s_node.metadata.labels
                and k8s_node.metadata.labels.get(HOSTNAME_LABEL) == sunbeam_name
            ):
                LOG.debug("K8S node %s missing tags", sunbeam_name)
                to_update[sunbeam_name] = str(k8s_node.metadata.name)

        return to_update

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        control = Role.CONTROL.name.lower()
        region_controller = Role.REGION_CONTROLLER.name.lower()
        if self.fqdn:
            node = self.client.cluster.get_node_info(self.fqdn)
            node_roles = node.get("role", [])
            if control not in node_roles and region_controller not in node_roles:
                return Result(ResultType.FAILED, f"{self.fqdn} is not a control node")
            control_nodes = [node]
        else:
            control_nodes = self.client.cluster.list_nodes_by_role(control)
        try:
            self.kube = get_kube_client(
                self.client,
            )
        except KubeClientError as e:
            LOG.debug("Failed to create k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        machines = self.jhelper.get_machines(self.model)

        try:
            self.to_update = self._get_k8s_node_to_update(control_nodes, machines)
        except SunbeamException:
            LOG.debug("Failed to get k8s nodes to update", exc_info=True)
            return Result(
                ResultType.FAILED,
                "Failed to get k8s nodes to update",
            )

        if not self.to_update:
            LOG.debug("No nodes to update")
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        for sunbeam_name, k8s_name in self.to_update.items():
            try:
                self.kube.apply(
                    core_v1.Node(
                        metadata=meta_v1.ObjectMeta(
                            name=k8s_name,
                            labels={
                                HOSTNAME_LABEL: sunbeam_name,
                            },
                        )
                    ),
                    field_manager=self.deployment.name,
                    force=True,
                )
            except l_exceptions.ApiError:
                LOG.debug("Failed to update node labels", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    f"Failed to update node labels for {sunbeam_name}",
                )

        return Result(ResultType.COMPLETED)


class RemoveK8SUnitsStep(RemoveMachineUnitsStep):
    """Remove K8S Unit."""

    _APPLICATION = APPLICATION
    _K8S_CONFIG_KEY = K8S_CONFIG_KEY
    _K8S_UNIT_TIMEOUT = K8S_UNIT_TIMEOUT

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            client,
            names,
            jhelper,
            self._K8S_CONFIG_KEY,
            self._APPLICATION,
            model,
            f"Remove {self._APPLICATION} unit",
            f"Removing {self._APPLICATION} unit from machine",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return self._K8S_UNIT_TIMEOUT


class AddK8SCloudStep(BaseStep, JujuStepHelper):
    _KUBECONFIG = K8S_KUBECONFIG_KEY

    def __init__(self, deployment: Deployment, jhelper: JujuHelper):
        super().__init__("Add K8S cloud", "Adding K8S cloud to Juju controller")
        self.client = deployment.get_client()
        self.jhelper = jhelper
        self.cloud_name = f"{deployment.name}{K8S_CLOUD_SUFFIX}"
        self.credential_name = f"{self.cloud_name}{CREDENTIAL_SUFFIX}"

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        clouds = self.jhelper.get_clouds()
        LOG.debug(f"Clouds registered in the controller: {clouds}")
        # TODO(hemanth): Need to check if cloud credentials are also created?
        if self.cloud_name in clouds.keys():
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Add k8s cloud to Juju controller."""
        try:
            kubeconfig = read_config(self.client, self._KUBECONFIG)
            self.jhelper.add_k8s_cloud(
                self.cloud_name, self.credential_name, kubeconfig
            )
        except (ConfigItemNotFoundException, UnsupportedKubeconfigException) as e:
            LOG.debug("Failed to add k8s cloud to Juju controller", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddK8SCloudInClientStep(BaseStep, JujuStepHelper):
    _KUBECONFIG = K8S_KUBECONFIG_KEY

    def __init__(self, deployment: Deployment):
        super().__init__("Add K8S cloud in client", "Adding K8S cloud to Juju client")
        self.client = deployment.get_client()
        self.cloud_name = f"{deployment.name}{K8S_CLOUD_SUFFIX}"
        self.credential_name = f"{self.cloud_name}{CREDENTIAL_SUFFIX}"

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        clouds = self.get_clouds("k8s", local=True)
        LOG.debug(f"Clouds registered in the client: {clouds}")
        if self.cloud_name in clouds:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Add microk8s clouds to Juju controller."""
        try:
            kubeconfig = read_config(self.client, self._KUBECONFIG)
            self.add_k8s_cloud_in_client(self.cloud_name, kubeconfig)
        except (ConfigItemNotFoundException, UnsupportedKubeconfigException) as e:
            LOG.debug("Failed to add k8s cloud to Juju client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class UpdateK8SCloudStep(BaseStep, JujuStepHelper):
    _KUBECONFIG = K8S_KUBECONFIG_KEY

    def __init__(self, deployment: Deployment, jhelper: JujuHelper):
        super().__init__("Update K8S cloud", "Updating K8S cloud in Juju controller")
        self.client = deployment.get_client()
        self.jhelper = jhelper
        self.cloud_name = f"{deployment.name}{K8S_CLOUD_SUFFIX}"
        self.credential_name = f"{self.cloud_name}{CREDENTIAL_SUFFIX}"

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        clouds = self.jhelper.get_clouds()
        LOG.debug(f"Clouds registered in the controller: {clouds}")
        if self.cloud_name not in clouds.keys():
            return Result(
                ResultType.SKIPPED,
                f"Cloud {self.cloud_name} is not found in the controller",
            )

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Add k8s cloud to Juju controller."""
        try:
            kubeconfig = read_config(self.client, self._KUBECONFIG)
            self.jhelper.update_k8s_cloud(self.cloud_name, kubeconfig)
        except (ConfigItemNotFoundException, UnsupportedKubeconfigException) as e:
            LOG.debug("Failed to add k8s cloud to Juju controller", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddK8SCredentialStep(BaseStep, JujuStepHelper):
    _KUBECONFIG = K8S_KUBECONFIG_KEY

    def __init__(self, deployment: Deployment, jhelper: JujuHelper):
        super().__init__(
            "Add k8s Credential", "Adding k8s credential to juju controller"
        )
        self.client = deployment.get_client()
        self.jhelper = jhelper
        self.cloud_name = f"{deployment.name}{K8S_CLOUD_SUFFIX}"
        self.credential_name = f"{self.cloud_name}{CREDENTIAL_SUFFIX}"

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            credentials = self.get_credentials(cloud=self.cloud_name)
        except subprocess.CalledProcessError as e:
            if "not found" in e.stderr:
                return Result(ResultType.COMPLETED)

            LOG.debug(e.stderr)
            LOG.exception("Error retrieving juju credentails from controller.")
            return Result(ResultType.FAILED, str(e))

        if self.credential_name in credentials.get("controller-credentials", {}).keys():
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Add k8s credential to Juju controller."""
        try:
            kubeconfig = read_config(self.client, self._KUBECONFIG)
            self.jhelper.add_k8s_credential(
                self.cloud_name, self.credential_name, kubeconfig
            )
        except (ConfigItemNotFoundException, UnsupportedKubeconfigException) as e:
            LOG.debug("Failed to add k8s cloud to Juju controller", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class StoreK8SKubeConfigStep(BaseStep, JujuStepHelper):
    _KUBECONFIG = K8S_KUBECONFIG_KEY

    def __init__(
        self, deployment: Deployment, client: Client, jhelper: JujuHelper, model: str
    ):
        super().__init__(
            "Store K8S kubeconfig",
            "Storing K8S configuration in sunbeam database",
        )
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.deployment = deployment

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            read_config(self.client, self._KUBECONFIG)
        except ConfigItemNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, context: StepContext) -> Result:
        """Store K8S config in clusterd."""
        try:
            unit = self.jhelper.get_leader_unit(APPLICATION, self.model)
            machine = self.jhelper.get_leader_unit_machine(APPLICATION, self.model)

            LOG.debug(unit)
            leader_unit_management_ip = self._get_management_server_ip(machine)
            LOG.debug("Leader unit management IP: %s", leader_unit_management_ip)
            run_action_kwargs = (
                {"server": leader_unit_management_ip}
                if leader_unit_management_ip
                else {}
            )
            result = self.jhelper.run_action(
                unit,
                self.model,
                "get-kubeconfig",
                run_action_kwargs,
            )

            LOG.debug(result)
            if not result.get("kubeconfig"):
                return Result(
                    ResultType.FAILED,
                    "ERROR: Failed to retrieve kubeconfig",
                )
            kubeconfig = yaml.safe_load(result["kubeconfig"])
            update_config(self.client, self._KUBECONFIG, kubeconfig)
        except (
            MachineNotFoundException,
            ApplicationNotFoundException,
            LeaderNotFoundException,
            ActionFailedException,
        ) as e:
            LOG.debug("Failed to store k8s config", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def _get_management_server_ip(self, machine_id: str) -> str | None:
        """API server endpoint for the Kubernetes cluster."""
        machine_interfaces = self.jhelper.get_machine_interfaces(self.model, machine_id)

        LOG.debug("Machine %r interfaces: %r", machine_id, machine_interfaces)
        management_space = self.deployment.get_space(Networks.MANAGEMENT)
        management_networks = self.jhelper.get_space_networks(
            self.model, management_space
        )

        for ip in _get_machines_space_ips(
            machine_interfaces, management_space, management_networks
        ):
            return ip + ":6443"
        return None


class _CommonK8SStepMixin:
    _SUBSTRATE: str = APPLICATION
    client: Client
    jhelper: JujuHelper
    model: str
    node: str

    def skip_checks(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        This method will:
        - Check if the node is a control node
        - Check if the application has been deployed
        - Find if a matching unit is running on the node
        - Define a kubeclient
        - Check if the node is present in the k8s cluster

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node_info = self.client.cluster.get_node_info(self.node)
        except NodeNotExistInClusterException:
            return Result(
                ResultType.SKIPPED,
                f"Node {self.node} not found in cluster",
            )

        control = Role.CONTROL.name.lower()
        region_controller = Role.REGION_CONTROLLER.name.lower()
        node_roles = node_info.get("role", "")
        if control not in node_roles and region_controller not in node_roles:
            LOG.debug("Node %s is not a control node", self.node)
            return Result(ResultType.SKIPPED)
        try:
            app = self.jhelper.get_application(self._SUBSTRATE, self.model)
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            return Result(
                ResultType.SKIPPED,
                f"Application {self._SUBSTRATE} has not been deployed yet",
            )

        for unit_name, unit in app.units.items():
            if unit.machine == str(node_info.get("machineid")):
                LOG.debug("Unit %s is running on node %s", unit_name, self.node)
                self.unit = unit_name
                break
        else:
            LOG.debug("No %s units found on %s", self._SUBSTRATE, self.node)
            return Result(ResultType.SKIPPED)

        try:
            kubeconfig = read_config(self.client, K8SHelper.get_kubeconfig_key())
        except ConfigItemNotFoundException:
            LOG.debug("K8S kubeconfig not found", exc_info=True)
            return Result(ResultType.FAILED, "K8S kubeconfig not found")

        self.kubeconfig = l_kubeconfig.KubeConfig.from_dict(kubeconfig)
        try:
            self.kube = l_client.Client(self.kubeconfig, self.model, trust_env=False)
        except l_exceptions.ConfigError as e:
            LOG.debug("Error creating k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            find_node(self.kube, self.node)
        except K8SNodeNotFoundError as e:
            LOG.debug("Node not found in k8s cluster")
            return Result(ResultType.SKIPPED, str(e))
        except K8SError as e:
            LOG.debug("Failed to find node in k8s cluster", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class MigrateK8SKubeconfigStep(BaseStep, _CommonK8SStepMixin):
    _SUBSTRATE: str = APPLICATION
    _KUBECONFIG: str = K8S_KUBECONFIG_KEY
    _ACTION: str = "get-kubeconfig"

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            "Migrate kubeconfig definition",
            "Migrate kubeconfig to another node",
        )
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model

    def _get_endpoint_from_kubeconfig(
        self, kubeconfig: "l_kubeconfig.KubeConfig"
    ) -> str:
        current_context = kubeconfig.current_context
        if current_context is None:
            raise K8SError("Current context not found in kubeconfig")

        context = kubeconfig.contexts.get(current_context)
        if context is None:
            raise K8SError("Context not found in kubeconfig")

        cluster = kubeconfig.clusters.get(context.cluster)
        if cluster is None:
            raise K8SError("Cluster not found in kubeconfig")
        return cluster.server

    def _extract_action_result(self, action_result: dict) -> str | None:
        return action_result.get("kubeconfig")

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        result = self.skip_checks()
        if result.result_type != ResultType.COMPLETED:
            return result

        try:
            current_endpoint = self._get_endpoint_from_kubeconfig(self.kubeconfig)
        except K8SError as e:
            return Result(ResultType.FAILED, str(e))

        action_result = self.jhelper.run_action(self.unit, self.model, self._ACTION)

        kubeconfig = self._extract_action_result(action_result)
        if not kubeconfig:
            return Result(
                ResultType.FAILED,
                "ERROR: Failed to retrieve kubeconfig",
            )
        current_node_kubeconfig = l_kubeconfig.KubeConfig.from_dict(
            yaml.safe_load(kubeconfig)
        )
        try:
            node_endpoint = self._get_endpoint_from_kubeconfig(current_node_kubeconfig)
        except K8SError as e:
            return Result(ResultType.FAILED, str(e))

        if current_endpoint != node_endpoint:
            # k8s endpoint register in k8s cloud is hosted on another node
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Migrate kubeconfig to another node."""
        try:
            app = self.jhelper.get_application(self._SUBSTRATE, self.model)
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            return Result(
                ResultType.SKIPPED,
                f"Application {self._SUBSTRATE} has not been deployed yet",
            )
        other_k8s = None
        for unit in app.units:
            if unit != self.unit:
                other_k8s = unit
                break
        if other_k8s is None:
            return Result(
                ResultType.FAILED,
                "No other k8s unit found to migrate kubeconfig",
            )
        try:
            action_result = self.jhelper.run_action(other_k8s, self.model, self._ACTION)
            kubeconfig = self._extract_action_result(action_result)
            if not kubeconfig:
                return Result(
                    ResultType.FAILED,
                    "ERROR: Failed to retrieve kubeconfig",
                )
            loaded_kubeconfig = yaml.safe_load(kubeconfig)
            update_config(self.client, self._KUBECONFIG, loaded_kubeconfig)
        except JujuException as e:
            LOG.debug("Failed to migrate kubeconfig", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class CheckApplicationK8SDistributionStep(BaseStep, _CommonK8SStepMixin):
    _CHARM: str
    _SUBSTRATE: str = APPLICATION

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        force: bool = False,
    ):
        if not hasattr(self, "_CHARM"):
            raise NotImplementedError("Subclasses must define _CHARM")
        super().__init__(
            f"Check {self._CHARM} distribution",
            f"Check if node is hosting units of {self._CHARM}",
        )
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model
        self.force = force

    def _fetch_apps(self) -> list[str]:
        try:
            model = self.jhelper.get_model_status(OPENSTACK_MODEL)
        except ModelNotFoundException:
            LOG.debug("Model not found, skipping")
            return []
        except JujuException as e:
            LOG.debug("Failed to get application names", exc_info=True)
            raise e

        app_names = []

        for name, app in model.apps.items():
            if not app:
                continue
            if app.charm_name == self._CHARM:
                app_names.append(name)

        return app_names

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        result = self.skip_checks()
        if result.result_type != ResultType.COMPLETED:
            return result

        try:
            apps = self._fetch_apps()
        except JujuException as e:
            return Result(ResultType.FAILED, str(e))

        for app in apps:
            app_label = {"app.kubernetes.io/name": app}
            pods = fetch_pods_for_eviction(self.kube, self.node, labels=app_label)
            nb_pods = len(pods)
            LOG.debug("Node %s has %d %s pods", self.node, nb_pods, app)
            if nb_pods > 0:
                total_pods = fetch_pods(self.kube, labels=app_label)
                if nb_pods == len(total_pods):
                    LOG.debug("All %s pods are on node %s", app, self.node)
                    if not self.force:
                        return Result(
                            ResultType.FAILED,
                            f"Node {self.node} has {nb_pods} {app} units, this will"
                            " lead to data loss and cluster failure if the units are "
                            " removed, use --force if you want to proceed",
                        )
                    LOG.warning(
                        "Node %s has %d %s pods, force flag is set, proceeding,"
                        " data loss and cluster failure may occur",
                        self.node,
                        nb_pods,
                        app,
                    )

        return Result(ResultType.COMPLETED)


class CheckMysqlK8SDistributionStep(CheckApplicationK8SDistributionStep):
    _CHARM = "mysql-k8s"
    _SUBSTRATE = APPLICATION


class CheckRabbitmqK8SDistributionStep(CheckApplicationK8SDistributionStep):
    _CHARM = "rabbitmq-k8s"
    _SUBSTRATE = APPLICATION


class CheckOvnK8SDistributionStep(CheckApplicationK8SDistributionStep):
    _CHARM = "ovn-central-k8s"
    _SUBSTRATE = APPLICATION


class CordonK8SUnitStep(BaseStep, _CommonK8SStepMixin):
    _SUBSTRATE: str = APPLICATION

    def __init__(self, client: Client, name: str, jhelper: JujuHelper, model: str):
        super().__init__("Cordon unit", "Prevent node from receiving new pods")
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return self.skip_checks()

    def run(self, context: StepContext) -> Result:
        """Cordon the unit."""
        self.update_status(context, "Cordoning unit")
        try:
            cordon(self.kube, self.node)
        except K8SError as e:
            LOG.debug("Failed to cordon unit", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class UncordonK8SUnitStep(BaseStep, _CommonK8SStepMixin):
    _SUBSTRATE: str = APPLICATION

    def __init__(self, client: Client, name: str, jhelper: JujuHelper, model: str):
        super().__init__("Uncordon unit", "Allow node to receive new pods")
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return self.skip_checks()

    def run(self, context: StepContext) -> Result:
        """Uncordon the unit."""
        self.update_status(context, "Uncordoning unit")
        try:
            uncordon(self.kube, self.node)
        except K8SError as e:
            LOG.debug("Failed to cordon unit", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DrainK8SUnitStep(BaseStep, _CommonK8SStepMixin):
    _SUBSTRATE: str = APPLICATION

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        remove_pvc: bool = False,
    ):
        super().__init__("Drain unit", "Drain node workloads")
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model
        self.remove_pvc = remove_pvc

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return self.skip_checks()

    @tenacity.retry(
        wait=tenacity.wait_fixed(20),
        stop=tenacity.stop_after_delay(600),
        retry=tenacity.retry_if_exception_type(ValueError),
        reraise=True,
    )
    def _wait_for_evicted_pods(self, kube: "l_client.Client", name: str):
        """Wait for pods to be evicted."""
        pods_for_eviction = fetch_pods_for_eviction(kube, name)
        LOG.debug("Pods for eviction: %d", len(pods_for_eviction))
        if pods_for_eviction:
            raise ValueError("Pods are still evicting")

    def run(self, context: StepContext) -> Result:
        """Drain the unit."""
        self.update_status(context, "Evicting workloads")
        try:
            drain(self.kube, self.node, remove_pvc=self.remove_pvc)
        except K8SError as e:
            LOG.debug("Failed to drain unit", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        self.update_status(context, "Waiting for workloads to leave")
        self._wait_for_evicted_pods(self.kube, self.node)

        return Result(ResultType.COMPLETED)


class DestroyK8SApplicationStep(DestroyMachineApplicationStep):
    """Destroy K8S application using Terraform."""

    _APPLICATION = APPLICATION
    _CONFIG_KEY = K8S_CONFIG_KEY

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            client,
            tfhelper,
            jhelper,
            manifest,
            self._CONFIG_KEY,
            [self._APPLICATION],
            model,
            f"Destroy {self._APPLICATION}",
            f"Destroying {self._APPLICATION}",
        )

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return K8S_DESTROY_TIMEOUT


class _PerHostK8SResourceStep(BaseStep):
    """Base class for steps that manage per-host k8s resources.

    Provides common logic for looking up control nodes, creating a kube client,
    finding the juju-space interface for each node, and determining which nodes
    have outdated resources.

    Subclasses must implement:
      - _get_outdated_resources(nodes, kube) -> (outdated, deleted)
      - run(context) -> Result
    """

    class _InterfaceError(SunbeamException):
        pass

    def __init__(
        self,
        name: str,
        description: str,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        network: Networks,
        kube_namespace: str | None = None,
        fqdn: str | None = None,
    ):
        super().__init__(name, description)
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.network = network
        self.kube_namespace = kube_namespace
        self.fqdn = fqdn
        self.to_update: list[dict] = []
        self.to_delete: list[dict] = []
        self._ifnames: dict[str, str] = {}

    def _get_interface(self, node: dict) -> str:
        """Get the network interface for the node in the configured space."""
        name = node["name"]
        if name in self._ifnames:
            return self._ifnames[name]
        machine_id = str(node["machineid"])
        machine_interfaces = self.jhelper.get_machine_interfaces(self.model, machine_id)
        LOG.debug("Machine %r interfaces: %r", machine_id, machine_interfaces)
        network_space = self.deployment.get_space(self.network)
        for ifname, iface in machine_interfaces.items():
            if (spaces := iface.space) and network_space in spaces.split():
                self._ifnames[name] = ifname
                return ifname
        raise self._InterfaceError(
            f"Node {node['name']} has no interface in {self.network.name} space"
        )

    def _get_outdated_resources(
        self, nodes: list[dict], kube: "l_client.Client"
    ) -> tuple[list[str], list[str]]:
        """Return (outdated, deleted) node name lists.

        Must be implemented by subclasses.
        """
        raise NotImplementedError

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not."""
        self.to_update = []
        self.to_delete = []
        control = Role.CONTROL.name.lower()
        region_controller = Role.REGION_CONTROLLER.name.lower()
        if self.fqdn:
            node = self.client.cluster.get_node_info(self.fqdn)
            node_roles = node.get("role", [])
            if control not in node_roles and region_controller not in node_roles:
                return Result(ResultType.FAILED, f"{self.fqdn} is not a control node")
            self.control_nodes = [node]
        else:
            self.control_nodes = self.client.cluster.list_nodes_by_role(control)

        try:
            self.kube = get_kube_client(self.client, self.kube_namespace)
        except KubeClientError as e:
            LOG.debug("Failed to create k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            outdated, deleted = self._get_outdated_resources(
                self.control_nodes, self.kube
            )
        except (l_exceptions.ApiError, self._InterfaceError) as e:
            LOG.debug("Failed to get outdated resources", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        if self.fqdn:
            # Single-node mode (join/bootstrap): only create/update for the
            # target node.  Defer deletion of stale resources to full
            # reconciliation (refresh, where fqdn is None).
            deleted = []

        if not (outdated or deleted):
            LOG.debug("No resources to update")
            return Result(ResultType.SKIPPED)

        for node in self.control_nodes:
            if node["name"] in outdated:
                self.to_update.append(node)

        # Deleted hostnames correspond to nodes no longer in control_nodes,
        # so we build synthetic entries with just the name for cleanup.
        for hostname in deleted:
            self.to_delete.append({"name": hostname})

        return Result(ResultType.COMPLETED)


class EnsureCiliumDeviceByHostStep(_PerHostK8SResourceStep):
    """Ensure each control node has a CiliumNodeConfig for its internal-space device."""

    _CILIUM_NAMESPACE = "kube-system"
    _RESTART_TIMEOUT = 300
    _RESTART_POLL_INTERVAL = 5
    _RESTART_PENDING_ANNOTATION = "sunbeam/restart-pending"

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        fqdn: str | None = None,
    ):
        super().__init__(
            "Ensure Cilium device config",
            "Ensuring Cilium device config per host",
            deployment,
            client,
            jhelper,
            model,
            Networks.INTERNAL,
            kube_namespace=self._CILIUM_NAMESPACE,
            fqdn=fqdn,
        )
        self.cilium_node_config_resource = (
            K8SHelper.get_lightkube_cilium_node_config_resource()
        )

    def _cilium_node_config_name(self, hostname: str) -> str:
        return f"cilium-devices-{hostname}"

    def _labels(self, hostname: str) -> dict[str, str]:
        return {
            "app.kubernetes.io/managed-by": self.deployment.name,
            HOSTNAME_LABEL: hostname,
        }

    def _get_outdated_resources(
        self, nodes: list[dict], kube: "l_client.Client"
    ) -> tuple[list[str], list[str]]:
        node_names = {node["name"] for node in nodes}
        outdated: list[str] = [node["name"] for node in nodes]
        deleted: list[str] = []

        configs = kube.list(
            self.cilium_node_config_resource,
            namespace=self._CILIUM_NAMESPACE,
            labels={"app.kubernetes.io/managed-by": self.deployment.name},
        )

        for config in configs:
            if config.metadata is None or config.metadata.labels is None:
                LOG.debug("CiliumNodeConfig has no metadata or labels")
                continue
            hostname = config.metadata.labels.get(HOSTNAME_LABEL)
            if hostname is None:
                LOG.debug(
                    "CiliumNodeConfig %s has no hostname label",
                    config.metadata.name,
                )
                continue
            if config.spec is None:
                LOG.debug("CiliumNodeConfig %r has no spec", hostname)
                continue
            if hostname not in node_names:
                LOG.debug(
                    "CiliumNodeConfig %s has no matching node",
                    config.metadata.name,
                )
                deleted.append(hostname)
                continue

            # Validate nodeSelector
            node_selector = config.spec.get("nodeSelector", {})
            match_labels = node_selector.get("matchLabels", {})
            if match_labels.get(HOSTNAME_LABEL) != hostname:
                LOG.debug(
                    "CiliumNodeConfig %s has wrong nodeSelector",
                    config.metadata.name,
                )
                continue

            # Validate device
            defaults = config.spec.get("defaults", {})
            interface = None
            for node in nodes:
                if node["name"] == hostname:
                    interface = self._get_interface(node)
            if not interface:
                LOG.debug(
                    "CiliumNodeConfig %s: no interface for node",
                    config.metadata.name,
                )
                continue
            if defaults.get("devices") != interface:
                LOG.debug(
                    "CiliumNodeConfig %s has wrong device (got %s, want %s)",
                    config.metadata.name,
                    defaults.get("devices"),
                    interface,
                )
                continue

            # Check if a previous restart failed and needs retry
            annotations = (
                config.metadata.annotations if config.metadata.annotations else {}
            )
            if annotations.get(self._RESTART_PENDING_ANNOTATION) == "true":
                LOG.debug(
                    "CiliumNodeConfig %s has pending restart",
                    config.metadata.name,
                )
                continue

            outdated.remove(hostname)
        return outdated, deleted

    def _resolve_k8s_node_name(self, sunbeam_name: str) -> str:
        """Resolve the K8s node name from the sunbeam hostname label.

        Sunbeam identifies nodes by FQDN while Kubernetes may use a short
        hostname for ``metadata.name`` and ``spec.nodeName``.  Look up the
        K8s node by its ``sunbeam/hostname`` label (set by
        ``EnsureK8SUnitsTaggedStep``) and return the authoritative
        ``metadata.name``.
        """
        try:
            nodes = list_nodes(self.kube, labels={HOSTNAME_LABEL: sunbeam_name})
        except K8SError as e:
            raise SunbeamException(
                f"Failed to resolve K8s node name for {sunbeam_name}"
            ) from e
        if not nodes:
            raise SunbeamException(
                f"No K8s node found with label {HOSTNAME_LABEL}={sunbeam_name}"
            )
        if len(nodes) > 1:
            names = [
                str(n.metadata.name) if n.metadata else "<no metadata>" for n in nodes
            ]
            raise SunbeamException(
                f"Multiple K8s nodes found with label "
                f"{HOSTNAME_LABEL}={sunbeam_name}: {names}"
            )
        node = nodes[0]
        if node.metadata is None or node.metadata.name is None:
            raise SunbeamException(f"K8s node for {sunbeam_name} has no metadata.name")
        return str(node.metadata.name)

    def _find_cilium_pod(self, k8s_node_name: str) -> "core_v1.Pod":
        pods = list(
            self.kube.list(
                core_v1.Pod,
                namespace=self._CILIUM_NAMESPACE,
                labels={"k8s-app": "cilium"},
            )
        )
        for pod in pods:
            if pod.spec and pod.spec.nodeName == k8s_node_name:
                return pod
        raise SunbeamException(f"No cilium pod found on node {k8s_node_name}")

    def _wait_for_cilium_ready(self, k8s_node_name: str, deleted_pod_name: str) -> None:
        """Wait until a NEW Ready cilium pod exists on the given node.

        Skips pods matching ``deleted_pod_name`` so the terminating pod
        cannot satisfy the readiness check.
        """
        deadline = time.monotonic() + self._RESTART_TIMEOUT
        while time.monotonic() < deadline:
            try:
                pods = list(
                    self.kube.list(
                        core_v1.Pod,
                        namespace=self._CILIUM_NAMESPACE,
                        labels={"k8s-app": "cilium"},
                    )
                )
            except l_exceptions.ApiError as e:
                raise SunbeamException(
                    f"Failed to list cilium pods during restart wait: {e}"
                ) from e

            for pod in pods:
                if pod.spec and pod.spec.nodeName == k8s_node_name:
                    pod_name = (
                        pod.metadata.name
                        if pod.metadata and pod.metadata.name
                        else None
                    )
                    if pod_name == deleted_pod_name:
                        continue  # skip the terminating pod
                    if pod.status and pod.status.conditions:
                        for condition in pod.status.conditions:
                            if condition.type == "Ready" and condition.status == "True":
                                LOG.debug(
                                    "New cilium pod %s on %s is Ready",
                                    pod_name,
                                    k8s_node_name,
                                )
                                return
            LOG.debug("Waiting for cilium pod on %s to be Ready", k8s_node_name)
            time.sleep(self._RESTART_POLL_INTERVAL)

        raise SunbeamException(
            f"Cilium pod on {k8s_node_name} did not become Ready "
            f"within {self._RESTART_TIMEOUT}s"
        )

    def _restart_cilium_on_node(self, sunbeam_node_name: str) -> None:
        k8s_node_name = self._resolve_k8s_node_name(sunbeam_node_name)
        pod = self._find_cilium_pod(k8s_node_name)
        pod_name = (
            pod.metadata.name if pod.metadata and pod.metadata.name else "unknown"
        )
        LOG.debug("Deleting cilium pod %s on node %s", pod_name, k8s_node_name)
        self.kube.delete(core_v1.Pod, pod_name, namespace=self._CILIUM_NAMESPACE)
        self._wait_for_cilium_ready(k8s_node_name, deleted_pod_name=pod_name)

    def run(self, context: StepContext) -> Result:
        """Apply or delete CiliumNodeConfig resources and restart cilium pods."""
        for node in self.to_update:
            name = node["name"]
            try:
                interface = self._get_interface(node)
            except MachineNotFoundException:
                LOG.debug(
                    "Failed to get machine for CiliumNodeConfig on %s",
                    name,
                    exc_info=True,
                )
                return Result(
                    ResultType.FAILED,
                    f"Machine not found for node {name}",
                )

            try:
                self.kube.apply(
                    self.cilium_node_config_resource(
                        metadata=meta_v1.ObjectMeta(
                            name=self._cilium_node_config_name(name),
                            labels=self._labels(name),
                            annotations={
                                self._RESTART_PENDING_ANNOTATION: "true",
                            },
                        ),
                        spec={
                            "nodeSelector": {
                                "matchLabels": {
                                    HOSTNAME_LABEL: name,
                                },
                            },
                            "defaults": {
                                "devices": interface,
                            },
                        },
                    ),
                    field_manager=self.deployment.name,
                    force=True,
                )
            except l_exceptions.ApiError:
                LOG.debug("Failed to apply CiliumNodeConfig", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    f"Failed to apply CiliumNodeConfig for {name}",
                )

            try:
                self._restart_cilium_on_node(name)
            except SunbeamException as e:
                return Result(ResultType.FAILED, str(e))

            # Clear restart-pending after successful restart
            try:
                self.kube.patch(
                    self.cilium_node_config_resource,
                    self._cilium_node_config_name(name),
                    {
                        "metadata": {
                            "annotations": {
                                self._RESTART_PENDING_ANNOTATION: "false",
                            }
                        }
                    },
                    namespace=self._CILIUM_NAMESPACE,
                    patch_type=l_patch_type.PatchType.MERGE,
                )
            except l_exceptions.ApiError:
                LOG.debug(
                    "Failed to clear restart-pending annotation for %s",
                    name,
                    exc_info=True,
                )

        for node in self.to_delete:
            name = node["name"]
            try:
                self.kube.delete(
                    self.cilium_node_config_resource,
                    self._cilium_node_config_name(name),
                    namespace=self._CILIUM_NAMESPACE,
                )
            except l_exceptions.ApiError:
                LOG.debug(
                    "Failed to delete CiliumNodeConfig for %s",
                    name,
                    exc_info=True,
                )
                continue

            try:
                self._restart_cilium_on_node(name)
            except SunbeamException:
                LOG.debug(
                    "Failed to restart cilium on %s after config deletion",
                    name,
                    exc_info=True,
                )
                continue

        return Result(ResultType.COMPLETED)


class EnsureL2AdvertisementByHostStep(_PerHostK8SResourceStep):
    """Ensure IP Pool is advertised by L2Advertisement resources."""

    _APPLICATION = APPLICATION

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        network: Networks,
        pool: str,
        fqdn: str | None = None,
    ):
        super().__init__(
            "Ensure L2 advertisement",
            "Ensuring L2 advertisement",
            deployment,
            client,
            jhelper,
            model,
            network,
            kube_namespace=K8SHelper.get_loadbalancer_namespace(),
            fqdn=fqdn,
        )
        self.pool = pool
        self.l2_advertisement_resource = (
            K8SHelper.get_lightkube_l2_advertisement_resource()
        )
        self.l2_advertisement_namespace = K8SHelper.get_loadbalancer_namespace()

    def _labels(self, name: str, space: str) -> dict[str, str]:
        """Return labels for the L2 advertisement."""
        return {
            "app.kubernetes.io/managed-by": self.deployment.name,
            "app.kubernetes.io/instance": self._instance_label(
                self.network.value.lower(), name
            ),
            "app.kubernetes.io/name": self._name_label(self.network.value.lower()),
            HOSTNAME_LABEL: name,
            "sunbeam/space": space,
            "sunbeam/network": self.network.value.lower(),
        }

    def _l2_advertisement_name(self, node: str) -> str:
        """Return L2 advertisement name for the node."""
        return f"{self.network.value.lower()}-{node}"

    def _name_label(self, network: str):
        """Return name label for the L2 advertisement."""
        return f"{network}-l2"

    def _instance_label(self, network: str, name: str):
        """Return instance label for the L2 advertisement."""
        return self._name_label(network) + "-" + name

    def _get_outdated_resources(
        self, nodes: list[dict], kube: "l_client.Client"
    ) -> tuple[list[str], list[str]]:
        """Get outdated L2 advertisement."""
        node_names = {node["name"] for node in nodes}
        outdated: list[str] = [node["name"] for node in nodes]
        deleted: list[str] = []

        l2_advertisements = kube.list(
            self.l2_advertisement_resource,
            namespace=self.l2_advertisement_namespace,
            labels={"app.kubernetes.io/name": self._name_label(self.pool)},
        )

        for l2_ad in l2_advertisements:
            if l2_ad.metadata is None or l2_ad.metadata.labels is None:
                LOG.debug("L2 advertisement has no metadata nor labels")
                continue
            hostname = l2_ad.metadata.labels.get(HOSTNAME_LABEL)

            if hostname is None:
                LOG.debug(
                    "L2 advertisement %s has no hostname label",
                    l2_ad.metadata.name,
                )
                continue
            if l2_ad.spec is None:
                LOG.debug("L2 advertisement %r has no spec", hostname)
                continue
            if hostname not in node_names:
                LOG.debug(
                    "L2 advertisement %s has no matching node",
                    l2_ad.metadata.name,
                )
                deleted.append(hostname)
                continue
            if self.pool not in l2_ad.spec.get("ipAddressPools", []):
                LOG.debug(
                    "L2 advertisement %s has wrong allocated ip pool",
                    l2_ad.metadata.name,
                )
                continue
            interface = None
            for node in nodes:
                if node["name"] == hostname:
                    interface = self._get_interface(node)
            if not interface:
                LOG.debug(
                    "L2 advertisement %s has no allocated interface",
                    l2_ad.metadata.name,
                )
                continue
            if l2_ad.spec.get("interfaces") != [interface]:
                LOG.debug(
                    "L2 advertisement %s has wrong allocated interface",
                    l2_ad.metadata.name,
                )
                continue
            outdated.remove(hostname)
        return outdated, deleted

    @tenacity.retry(
        wait=tenacity.wait_fixed(15),
        stop=tenacity.stop_after_delay(600),
        retry=tenacity.retry_if_exception_type(tenacity.TryAgain),
        reraise=True,
    )
    def _ensure_l2_advertisement(self, name: str, interface: str):
        try:
            self.kube.apply(
                self.l2_advertisement_resource(
                    metadata=meta_v1.ObjectMeta(
                        name=self._l2_advertisement_name(name),
                        labels=self._labels(
                            name, self.deployment.get_space(self.network)
                        ),
                    ),
                    spec={
                        "ipAddressPools": [self.pool],
                        "interfaces": [interface],
                        "nodeSelectors": [
                            {
                                "matchLabels": {
                                    HOSTNAME_LABEL: name,
                                }
                            }
                        ],
                    },
                ),
                field_manager=self.deployment.name,
                force=True,
            )
        except l_exceptions.ApiError as e:
            if e.status.code == 500 and "failed calling webhook" in str(e.status):
                raise tenacity.TryAgain("Trying to patch again")
            raise

    def run(self, context: StepContext) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        node_not_found = []

        for node in self.to_update:
            name = node["name"]
            try:
                interface = self._get_interface(node)
            except MachineNotFoundException:
                LOG.debug(
                    "Failed to get the machine for L2 advertisement on %s",
                    name,
                    exc_info=True,
                )
                node_not_found.append(name)
                continue

            try:
                self._ensure_l2_advertisement(name, interface)
            except l_exceptions.ApiError:
                LOG.debug("Failed to update L2 advertisement", exc_info=True)
                return Result(
                    ResultType.FAILED, f"Failed to update L2 advertisement for {name}"
                )

        if node_not_found:
            return Result(
                ResultType.SKIPPED,
                "Failed to get machines for L2 advertisement on nodes: "
                + ", ".join(node_not_found),
            )

        for node in self.to_delete:
            try:
                self.kube.delete(
                    self.l2_advertisement_resource,
                    self._l2_advertisement_name(node["name"]),
                    namespace=self.l2_advertisement_namespace,
                )
            except l_exceptions.ApiError:
                LOG.debug("Failed to delete L2 advertisement", exc_info=True)
                continue

        return Result(ResultType.COMPLETED)


class EnsureDefaultL2AdvertisementMutedStep(BaseStep):
    """Ensure default l2 advertisement is muted."""

    _APPLICATION = APPLICATION

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Mute default L2 advertisement",
            "Ensuring default L2 advertisement is muted",
        )
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.l2_advertisement_resource = (
            K8SHelper.get_lightkube_l2_advertisement_resource()
        )
        self.l2_advertisement_namespace = K8SHelper.get_loadbalancer_namespace()
        # Default l2 advertisement has the name of the pool
        self.default_l2_advertisement = K8SHelper.get_internal_pool_name()
        self.node_selectors = [
            {
                "matchLabels": {
                    "kubernetes.io/hostname": "not-existing.sunbeam",
                }
            }
        ]

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.kube = get_kube_client(
                self.client,
                self.l2_advertisement_namespace,
            )
        except KubeClientError as e:
            LOG.debug("Failed to create k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            l2_advertisement = self.kube.get(
                self.l2_advertisement_resource,
                self.default_l2_advertisement,
                namespace=self.l2_advertisement_namespace,
            )
        except l_exceptions.ApiError as e:
            if e.status.code == 404:
                LOG.debug("L2 advertisement not found, skipping")
                return Result(ResultType.SKIPPED)
            LOG.debug("Failed to get default L2 advertisement", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        if (
            l2_advertisement.spec
            and l2_advertisement.spec.get("nodeSelectors") == self.node_selectors
        ):
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            self.kube.apply(
                self.l2_advertisement_resource(
                    metadata=meta_v1.ObjectMeta(
                        name=self.default_l2_advertisement,
                    ),
                    spec={
                        "nodeSelectors": self.node_selectors,
                    },
                ),
                field_manager=self.deployment.name,
                force=True,
            )
        except l_exceptions.ApiError:
            LOG.debug("Failed to update default L2 advertisement", exc_info=True)
            return Result(
                ResultType.FAILED, "Failed to update L2 default advertisement"
            )

        return Result(ResultType.COMPLETED)


class PatchCoreDNSStep(BaseStep):
    """Ensure HA for Coredns.

    This is a workaround for https://github.com/canonical/k8s-operator/issues/504
    Resources and HPA settings are updated for coredns
    There is a fix in k8s snap but not backported. Here is the bug link
    https://github.com/canonical/k8s-snap/issues/2456
    TODO: Remove this class once #2456 is fixed and available in k8s 1.32/stable
    """

    def __init__(
        self,
        deployment: Deployment,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Patch Coredns resources and horizontal pod autoscaling",
            "Patching Coredns resources and horizontal pod autoscaling",
        )
        self.deployment = deployment
        self.client = deployment.get_client()
        self.jhelper = jhelper
        self.juju_app_name = "k8s"
        self.coredns_namespace = "kube-system"
        self.coredns_hpa = "ck-dns-coredns"
        self.coredns_deployment = "coredns"
        self.timeout = 180  # 3 minutes for helm upgrade
        self.replica_count = 1

    _COREDNS_POLL_INTERVAL = 10

    def compute_coredns_replica_count(self, control_nodes: int) -> int:
        """Determine replica count for coredns."""
        return 1 if control_nodes < 3 else 3

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.kube = get_kube_client(
                self.client,
                self.coredns_namespace,
            )
        except KubeClientError as e:
            LOG.debug("Failed to create k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        control_nodes = self.client.cluster.list_nodes_by_role("control")
        self.replica_count = self.compute_coredns_replica_count(len(control_nodes))

        try:
            coredns_hpa = self.kube.get(
                autoscaling_v2.HorizontalPodAutoscaler, name=self.coredns_hpa
            )
            LOG.debug(f"Existing coredns hpa: {coredns_hpa}")
            coredns_hpa_spec = coredns_hpa.spec
            if coredns_hpa_spec is None:
                LOG.debug("Coredns HPA has no spec")
                return Result(ResultType.COMPLETED)

            if self.replica_count == coredns_hpa_spec.minReplicas:
                return Result(ResultType.SKIPPED)
        except l_exceptions.ApiError as e:
            if "not found" not in str(e):
                LOG.debug("Failed to get coredns hpa", exc_info=True)
                return Result(ResultType.FAILED, str(e))
            else:
                LOG.debug(f"No hpa found for coredns: {str(e)}")

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        try:
            leader = self.jhelper.get_leader_unit(
                self.juju_app_name, self.deployment.openstack_machines_model
            )
        except JujuException as e:
            LOG.debug(f"Failed to get {self.juju_app_name} leader", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        hpa_dict = copy.deepcopy(COREDNS_HPA)
        hpa_dict["minReplicas"] = self.replica_count
        hpa = json.dumps(hpa_dict)
        resources = json.dumps(COREDNS_RESOURCES)
        pdb = json.dumps(COREDNS_PDB)
        # Note: Applying coredns hpa with modified minReplica will take time
        # to see the scale in, scale out based on policy defined in COREDNS_HPA
        try:
            set_json = (
                f"hpa='{hpa}',resources='{resources}',podDisruptionBudget='{pdb}'"
            )
            cmd_str = (
                f"k8s helm upgrade -n {self.coredns_namespace} ck-dns "
                "/snap/k8s/current/k8s/manifests/charts/coredns-*.tgz"
                f" --reuse-values --set-json {set_json}"
            )
            LOG.debug(f"Running cmd in unit {leader}: {cmd_str}")

            result = self.jhelper.run_cmd_on_machine_unit_payload(
                leader,
                self.deployment.openstack_machines_model,
                cmd_str,
                self.timeout,
            )
            LOG.debug(f"Result: {result}")

            if result.return_code != 0:
                return Result(
                    ResultType.FAILED,
                    f"Error in setting coredns hpa: {result.stderr}",
                )
        except JujuException as e:
            LOG.info(
                "Failed to run helm upgrade on coredns",
                exc_info=True,
            )
            return Result(ResultType.FAILED, str(e))

        # Wait for CoreDNS pods to be ready rather than relying on the k8s
        # charm status, which may not reflect external changes (helm upgrade)
        # until the next update-status hook fires.
        try:
            self._wait_for_coredns_ready()
        except (TimeoutError, K8SError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def _wait_for_coredns_ready(self) -> None:
        """Wait until CoreDNS deployment has at least replica_count available replicas.

        Directly polls the CoreDNS Deployment status rather than relying on the
        k8s charm status, which may not reflect external changes (e.g. a helm
        upgrade) until the next update-status hook runs.
        """
        deadline = time.monotonic() + K8S_APP_TIMEOUT
        while time.monotonic() < deadline:
            try:
                deployment = self.kube.get(
                    apps_v1.Deployment, name=self.coredns_deployment
                )
            except l_exceptions.ApiError as e:
                status = getattr(e, "status", None)
                status_code = getattr(status, "code", None)
                if status_code == 404:
                    raise K8SError(
                        f"CoreDNS deployment '{self.coredns_deployment}' not found"
                    ) from e
                if status_code is not None and 500 <= status_code < 600:
                    LOG.debug("Transient error while getting coredns deployment: %s", e)
                    time.sleep(self._COREDNS_POLL_INTERVAL)
                    continue
                raise
            available_replicas = (
                deployment.status.availableReplicas
                if deployment.status and deployment.status.availableReplicas is not None
                else 0
            )
            if available_replicas >= self.replica_count:
                LOG.debug(
                    "CoreDNS deployment ready: %d/%d replicas available",
                    available_replicas,
                    self.replica_count,
                )
                return
            LOG.debug(
                "Waiting for CoreDNS availableReplicas >= %d (current: %d)",
                self.replica_count,
                available_replicas,
            )
            time.sleep(self._COREDNS_POLL_INTERVAL)
        raise TimeoutError(
            f"CoreDNS deployment did not reach {self.replica_count} available"
            f" replicas within {K8S_APP_TIMEOUT}s"
        )


class PatchServiceExternalTrafficStep(BaseStep):
    """Patch service external traffic policy to Local.

    This is a workaround for LP#2111922.
    Using externalTrafficPolicy will force metallb to announce
    from nodes hosting the pods of the deployment. Since Juju does
    not support HA on k8s, only the node hosting the Juju pod will be
    eligible to announce the service IP. This will that the connection
    isn't broken when new k8s node join, as the mac address will not change
    uncontrollably.
    """

    def __init__(
        self,
        deployment: Deployment,
        service_name: str,
        namespace: str,
    ):
        super().__init__(
            "Patch Service Traffic announcement",
            "Patching Service Traffic announcement",
        )
        self.deployment = deployment
        self.client = deployment.get_client()
        self.service_name = service_name
        self.namespace = namespace

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.kube = get_kube_client(
                self.client,
                self.namespace,
            )
        except KubeClientError as e:
            LOG.debug("Failed to create k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            service = self.kube.get(core_v1.Service, name=self.service_name)
            if service.spec is None or service.spec.externalTrafficPolicy == "Local":
                return Result(ResultType.SKIPPED)
        except l_exceptions.ApiError as e:
            LOG.debug("Failed to get service", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Run the step to completion."""
        try:
            service = self.kube.get(core_v1.Service, name=self.service_name)
            if service.spec is None:
                LOG.debug("Service has no spec")
                return Result(ResultType.FAILED, "Service has no spec")

            service.spec.externalTrafficPolicy = "Local"
            self.kube.patch(
                core_v1.Service,
                name=self.service_name,
                namespace=self.namespace,
                obj={
                    "spec": {
                        "externalTrafficPolicy": "Local",
                    }
                },
            )
        except (l_exceptions.ApiError, K8SError) as e:
            LOG.debug("Failed to patch service", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
