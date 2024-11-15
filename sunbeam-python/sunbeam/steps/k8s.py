# Copyright (c) 2024 Canonical Ltd.
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
import subprocess

import tenacity
import yaml
from lightkube import ConfigError, KubeConfig
from lightkube.core.client import Client as KubeClient
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
    ModelNotFoundException,
    UnsupportedKubeconfigException,
    run_sync,
)
from sunbeam.core.k8s import (
    CREDENTIAL_SUFFIX,
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
        config_tfvars: dict[str, bool | str] = {
            "load-balancer-enabled": True,
            "load-balancer-l2-mode": True,
        }

        charm_manifest = self.manifest.core.software.charms.get("k8s")
        if charm_manifest and charm_manifest.config:
            config_tfvars.update(charm_manifest.config)

        lb_range = self._get_loadbalancer_range()
        if lb_range:
            config_tfvars["load-balancer-cidrs"] = lb_range

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

    def __init__(self, deployment: Deployment, jhelper: JujuHelper):
        super().__init__("Add K8S cloud in client", "Adding K8S cloud to Juju client")
        self.client = deployment.get_client()
        self.jhelper = jhelper
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

    def __init__(self, client: Client, jhelper: JujuHelper, model: str):
        super().__init__(
            "Store K8S kubeconfig",
            "Storing K8S configuration in sunbeam database",
        )
        self.client = client
        self.jhelper = jhelper
        self.model = model

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
            LOG.debug(unit)
            result = run_sync(
                self.jhelper.run_action(unit, self.model, "get-kubeconfig")
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
            ApplicationNotFoundException,
            LeaderNotFoundException,
            ActionFailedException,
        ) as e:
            LOG.debug("Failed to store k8s config", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


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
        try:
            app = run_sync(self.jhelper.get_application(self._SUBSTRATE, self.model))
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
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
            return Result(ResultType.SKIPPED)

        try:
            kubeconfig = read_config(self.client, K8SHelper.get_kubeconfig_key())
        except ConfigItemNotFoundException:
            LOG.debug("K8S kubeconfig not found", exc_info=True)
            return Result(ResultType.FAILED, "K8S kubeconfig not found")

        self.kubeconfig = KubeConfig.from_dict(kubeconfig)
        try:
            self.kube = KubeClient(self.kubeconfig, self.model, trust_env=False)
        except ConfigError as e:
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

    def _get_endpoint_from_kubeconfig(self, kubeconfig: KubeConfig) -> str:
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
        current_node_kubeconfig = KubeConfig.from_dict(yaml.safe_load(kubeconfig))
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
        try:
            app = run_sync(self.jhelper.get_application(self._SUBSTRATE, self.model))
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            return Result(
                ResultType.SKIPPED,
                f"Application {self._SUBSTRATE} has not been deployed yet",
            )
        other_k8s = None
        for unit in app.units:
            if unit.name != self.unit.name:
                other_k8s = unit.name
                break
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
    def _wait_for_evicted_pods(self, kube: KubeClient, name: str):
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
