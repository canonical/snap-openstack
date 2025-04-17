# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import logging
import subprocess
import typing

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
    run_sync,
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
    AddMachineUnitsStep,
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
    from lightkube.models import meta_v1
    from lightkube.resources import core_v1
else:
    l_kubeconfig = LazyImport("lightkube.config.kubeconfig")
    l_client = LazyImport("lightkube.core.client")
    l_exceptions = LazyImport("lightkube.core.exceptions")
    meta_v1 = LazyImport("lightkube.models.meta_v1")
    core_v1 = LazyImport("lightkube.resources.core_v1")


LOG = logging.getLogger(__name__)
K8S_CONFIG_KEY = "TerraformVarsK8S"
K8S_ADDONS_CONFIG_KEY = "TerraformVarsK8SAddons"
APPLICATION = "k8s"
K8S_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
K8S_DESTROY_TIMEOUT = 900
K8S_UNIT_TIMEOUT = 1200  # 20 minutes, adding / removing units can take a long time
K8S_ENABLE_ADDONS_TIMEOUT = 180  # 3 minutes
K8SD_SNAP_SOCKET = "/var/snap/k8s/common/var/lib/k8sd/state/control.socket"


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


def _get_kube_client(client: Client, namespace: str | None = None) -> "l_client.Client":
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
            "Deploy K8S",
            "Deploying K8S",
            refresh,
        )

        self.accept_defaults = accept_defaults
        self.variables: dict = {}
        self.ranges: str | None = None

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

        LOG.debug(self.variables)
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

        return config_tfvars

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        return {
            "endpoint_bindings": [
                {"space": self.deployment.get_space(Networks.MANAGEMENT)},
            ],
            "k8s_config": self._get_k8s_config_tfvars(),
        }


class AddK8SUnitsStep(AddMachineUnitsStep):
    """Add K8S Unit."""

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
            K8S_CONFIG_KEY,
            APPLICATION,
            model,
            "Add K8S unit",
            "Adding K8S unit to machine",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return K8S_UNIT_TIMEOUT


class EnsureK8SUnitsTaggedStep(BaseStep):
    """Ensure K8S units get properly tagged.

    This step ensures that evey k8s nodes is tagged with the
    HOSTNAME_LABEL, to ensure sunbeam can query the correct nodes
    afterwards.
    Match is done on management ip address. Node IP in k8s is guaranteed by
    the cluster space binding, which is always bound to the management
    space.
    """

    def __init__(
        self, deployment: Deployment, client: Client, jhelper: JujuHelper, model: str
    ):
        super().__init__(
            "Ensure K8S units tagged", "Ensuring K8S units are properly tagged"
        )
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.to_update: dict[str, str] = {}

    def _get_management_ips(self, juju_machine: dict) -> list[str]:
        management_ips = []
        management_space = self.deployment.get_space(Networks.MANAGEMENT)
        for iface in juju_machine["network-interfaces"].values():
            if iface.get("space") == management_space:
                management_ips.extend(iface.get("ip-addresses", []))
        return management_ips

    def _find_matching_k8s_node(
        self,
        k8s_nodes: list["core_v1.Node"],
        hostname: str,
        ips: list[str],
    ) -> "core_v1.Node | None":
        for k8s_node in k8s_nodes:
            if k8s_node.metadata is None:
                LOG.debug("K8S node has no metadata, %s", k8s_node)
                continue

            if k8s_node.metadata.name == hostname:
                return k8s_node

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
                        if address.address == hostname:
                            return k8s_node
        return None

    def _get_k8s_node_to_update(
        self, nodes: list[dict], k8s_nodes: list["core_v1.Node"], machines: dict
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
            management_ips = self._get_management_ips(juju_machine)
            if not management_ips:
                LOG.debug("No management IPs found for machine %s", machine_id)
                raise SunbeamException(f"{sunbeam_name!r} has no management IPs")
            k8s_node = self._find_matching_k8s_node(
                k8s_nodes, sunbeam_name, management_ips
            )
            if not k8s_node:
                LOG.debug(
                    "No matching k8s node found for %s, management IPs %s",
                    sunbeam_name,
                    management_ips,
                )
                raise SunbeamException(f"{sunbeam_name} has no matching k8s node")
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        control_nodes = self.client.cluster.list_nodes_by_role(
            Role.CONTROL.name.lower()
        )
        try:
            self.kube = _get_kube_client(
                self.client,
            )
        except KubeClientError as e:
            LOG.debug("Failed to create k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            k8s_nodes = list(
                self.kube.list(
                    core_v1.Node,
                    labels={DEPLOYMENT_LABEL: self.deployment.name},
                )
            )
        except l_exceptions.ApiError:
            LOG.debug("Failed to fetch k8s nodes", exc_info=True)
            return Result(
                ResultType.FAILED,
                "Failed to get nodes",
            )

        machines = run_sync(self.jhelper.get_machines_status(self.model))

        try:
            self.to_update = self._get_k8s_node_to_update(
                control_nodes, k8s_nodes, machines
            )
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

    def run(self, status: Status | None) -> Result:
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        clouds = run_sync(self.jhelper.get_clouds())
        LOG.debug(f"Clouds registered in the controller: {clouds}")
        # TODO(hemanth): Need to check if cloud credentials are also created?
        if f"cloud-{self.cloud_name}" in clouds.keys():
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Add k8s cloud to Juju controller."""
        try:
            kubeconfig = read_config(self.client, self._KUBECONFIG)
            run_sync(
                self.jhelper.add_k8s_cloud(
                    self.cloud_name, self.credential_name, kubeconfig
                )
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        clouds = self.get_clouds("k8s", local=True)
        LOG.debug(f"Clouds registered in the client: {clouds}")
        if self.cloud_name in clouds:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        clouds = run_sync(self.jhelper.get_clouds())
        LOG.debug(f"Clouds registered in the controller: {clouds}")
        if f"cloud-{self.cloud_name}" not in clouds.keys():
            return Result(
                ResultType.FAILED, f"Cloud {self.cloud_name} not found in controller"
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Add k8s cloud to Juju controller."""
        try:
            kubeconfig = read_config(self.client, self._KUBECONFIG)
            run_sync(self.jhelper.update_k8s_cloud(self.cloud_name, kubeconfig))
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

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
        """Add k8s credential to Juju controller."""
        try:
            kubeconfig = read_config(self.client, self._KUBECONFIG)
            run_sync(
                self.jhelper.add_k8s_credential(
                    self.cloud_name, self.credential_name, kubeconfig
                )
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            read_config(self.client, self._KUBECONFIG)
        except ConfigItemNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Store K8S config in clusterd."""
        try:
            unit = run_sync(self.jhelper.get_leader_unit(APPLICATION, self.model))
            machine = run_sync(
                self.jhelper.get_leader_unit_machine(APPLICATION, self.model)
            )
            LOG.debug(unit)
            leader_unit_management_ip = self._get_management_server_ip(machine)
            LOG.debug("Leader unit management IP: %s", leader_unit_management_ip)
            run_action_kwargs = (
                {"action_params": {"server": leader_unit_management_ip}}
                if leader_unit_management_ip
                else {}
            )
            result = run_sync(
                self.jhelper.run_action(
                    unit,
                    self.model,
                    "get-kubeconfig",
                    **run_action_kwargs,
                )
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
        machine_interfaces = run_sync(
            self.jhelper.get_machine_interfaces(self.model, machine_id)
        )
        LOG.debug("Machine %r interfaces: %r", machine_id, machine_interfaces)
        for ifname, iface in machine_interfaces.items():
            if iface.get("space") == self.deployment.get_space(Networks.MANAGEMENT):
                return iface["ip-addresses"][0] + ":6443"
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
            return Result(ResultType.FAILED, f"Node {self.node} not found in cluster")
        if Role.CONTROL.name.lower() not in node_info.get("role", ""):
            LOG.debug("Node %s is not a control node", self.node)
            return Result(ResultType.SKIPPED)
        model = run_sync(self.jhelper.get_model(self.model))
        try:
            app = run_sync(self.jhelper.get_application(self._SUBSTRATE, model))
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            run_sync(model.disconnect())
            return Result(
                ResultType.SKIPPED,
                f"Application {self._SUBSTRATE} has not been deployed yet",
            )

        for unit in app.units:
            if unit.machine.id == str(node_info.get("machineid")):
                LOG.debug("Unit %s is running on node %s", unit.name, self.node)
                self.unit = unit
                break
        else:
            LOG.debug("No %s units found on %s", self._SUBSTRATE, self.node)
            run_sync(model.disconnect())
            return Result(ResultType.SKIPPED)

        run_sync(model.disconnect())
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

    def is_skip(self, status: Status | None = None) -> Result:
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

        action_result = run_sync(
            self.jhelper.run_action(self.unit.name, self.model, self._ACTION)
        )
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

    def run(self, status: Status | None = None) -> Result:
        """Migrate kubeconfig to another node."""
        model = run_sync(self.jhelper.get_model(self.model))
        try:
            app = run_sync(self.jhelper.get_application(self._SUBSTRATE, model))
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            run_sync(model.disconnect())
            return Result(
                ResultType.SKIPPED,
                f"Application {self._SUBSTRATE} has not been deployed yet",
            )
        other_k8s = None
        for unit in app.units:
            if unit.name != self.unit.name:
                other_k8s = unit.name
                break
        run_sync(model.disconnect())
        if other_k8s is None:
            return Result(
                ResultType.FAILED,
                "No other k8s unit found to migrate kubeconfig",
            )
        try:
            action_result = run_sync(
                self.jhelper.run_action(other_k8s, self.model, self._ACTION)
            )
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
            model = run_sync(self.jhelper.get_model(OPENSTACK_MODEL))
        except ModelNotFoundException:
            LOG.debug("Model not found, skipping")
            return []
        except JujuException as e:
            LOG.debug("Failed to get application names", exc_info=True)
            raise e

        app_names = []

        for name, app in model.applications.items():
            if not app:
                continue
            if app.charm_name == self._CHARM:
                app_names.append(name)

        run_sync(model.disconnect())

        return app_names

    def is_skip(self, status: Status | None = None) -> Result:
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return self.skip_checks()

    def run(self, status: Status | None = None) -> Result:
        """Cordon the unit."""
        self.update_status(status, "Cordoning unit")
        try:
            cordon(self.kube, self.node)
        except K8SError as e:
            LOG.debug("Failed to cordon unit", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DrainK8SUnitStep(BaseStep, _CommonK8SStepMixin):
    _SUBSTRATE: str = APPLICATION

    def __init__(self, client: Client, name: str, jhelper: JujuHelper, model: str):
        super().__init__("Drain unit", "Drain node workloads")
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, status: Status | None = None) -> Result:
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

    def run(self, status: Status | None = None) -> Result:
        """Drain the unit."""
        self.update_status(status, "Evicting workloads")
        try:
            drain(self.kube, self.node)
        except K8SError as e:
            LOG.debug("Failed to drain unit", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        self.update_status(status, "Waiting for workloads to leave")
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


class EnsureL2AdvertisementByHostStep(BaseStep):
    """Ensure IP Pool is advertised by L2Advertisement resources."""

    _APPLICATION = APPLICATION

    class _L2AdvertisementError(SunbeamException):
        pass

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        network: Networks,
        pool: str,
    ):
        super().__init__("Ensure L2 advertisement", "Ensuring L2 advertisement")
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.network = network
        self.pool = pool
        self.l2_advertisement_resource = (
            K8SHelper.get_lightkube_l2_advertisement_resource()
        )
        self.l2_advertisement_namespace = K8SHelper.get_loadbalancer_namespace()
        self.to_update: list[dict] = []
        self.to_delete: list[dict] = []
        self._ifnames: dict[str, str] = {}

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

    def _get_outdated_l2_advertisement(
        self, nodes: list[dict], kube: "l_client.Client"
    ) -> tuple[list[str], list[str]]:
        """Get outdated L2 advertisement."""
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
            if hostname not in outdated:
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
                    interface = self._get_interface(node, self.network)
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

    def _get_interface(
        self,
        node: dict,
        network: Networks,
    ) -> str:
        """Get interface for the node depending on the pool."""
        name = node["name"]
        if name in self._ifnames:
            return self._ifnames[name]
        machine_id = str(node["machineid"])
        machine_interfaces = run_sync(
            self.jhelper.get_machine_interfaces(self.model, machine_id)
        )
        LOG.debug("Machine %r interfaces: %r", machine_id, machine_interfaces)
        for ifname, iface in machine_interfaces.items():
            if iface.get("space") == self.deployment.get_space(network):
                self._ifnames[name] = ifname
                return ifname
        raise self._L2AdvertisementError(
            f"Node {node['name']} has no interface in {network.name} space"
        )

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.control_nodes = self.client.cluster.list_nodes_by_role(
            Role.CONTROL.name.lower()
        )

        try:
            self.kube = _get_kube_client(
                self.client,
                self.l2_advertisement_namespace,
            )
        except KubeClientError as e:
            LOG.debug("Failed to create k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            outdated, deleted = self._get_outdated_l2_advertisement(
                self.control_nodes, self.kube
            )
        except (l_exceptions.ApiError, self._L2AdvertisementError) as e:
            LOG.debug("Failed to get outdated L2 advertisement", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        if not (outdated or deleted):
            LOG.debug("No L2 advertisement to update")
            return Result(ResultType.SKIPPED)

        for node in self.control_nodes:
            if node["name"] in outdated:
                self.to_update.append(node)
            if node["name"] in deleted:
                self.to_delete.append(node)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        for node in self.to_update:
            name = node["name"]
            interface = self._get_interface(node, self.network)
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
            except l_exceptions.ApiError:
                LOG.debug("Failed to update L2 advertisement", exc_info=True)
                return Result(
                    ResultType.FAILED, f"Failed to update L2 advertisement for {name}"
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.kube = _get_kube_client(
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

    def run(self, status: Status | None) -> Result:
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
