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
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.terraform import TerraformException, TerraformHelper
from sunbeam.jobs import questions
from sunbeam.jobs.common import BaseStep, Result, ResultType, read_config, update_config
from sunbeam.jobs.juju import (
    MODEL,
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    LeaderNotFoundException,
    TimeoutException,
    run_sync,
)

LOG = logging.getLogger(__name__)
MICROK8S_CLOUD = "sunbeam-microk8s"
APPLICATION = "microk8s"
MICROK8S_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
MICROK8S_UNIT_TIMEOUT = 1200  # 20 minutes, adding / removing units can take a long time
CREDENTIAL_SUFFIX = "-creds"
MICROK8S_DEFAULT_STORAGECLASS = "microk8s-hostpath"
CONTAINERD_ENV_TEMPLATE = """
# This file is managed by Juju. Manual changes may be lost at any time.

# Configure limits for locked memory and maximum number of open files
ulimit -n 65536 || true
ulimit -l 16384 || true

HTTP_RPOXY={http_proxy}
HTTPS_PROXY={https_proxy}
NO_PROXY={no_proxy}
"""
CONFIG_KEY = "Microk8sConfig"
MICROK8S_ADDONS_CONFIG_KEY = "TerraformVarsMicrok8sAddons"


def validate_metallb_range(ip_ranges: str):
    for ip_range in ip_ranges.split(","):
        ips = ip_range.split("-")
        if len(ips) == 1:
            if "/" not in ips[0]:
                raise ValueError(
                    "Invalid CIDR definition, must be in the form 'ip/mask'"
                )
            ipaddress.ip_network(ips[0])
        elif len(ips) == 2:
            ipaddress.ip_address(ips[0])
            ipaddress.ip_address(ips[1])
        else:
            raise ValueError(
                "Invalid IP range, must be in the form of 'ip-ip' or 'cidr'"
            )


def microk8s_addons_questions():
    return {
        "metallb": questions.PromptQuestion(
            "MetalLB address allocation range "
            "(supports multiple ranges, comma separated)",
            default_value="10.20.21.10-10.20.21.20",
            validation_function=validate_metallb_range,
        ),
    }


class DeployMicrok8sApplicationStep(BaseStep, JujuStepHelper):
    """Deploy Microk8s application using Terraform"""

    _CONFIG = MICROK8S_ADDONS_CONFIG_KEY

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        preseed_file: Optional[Path] = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Deploy MicroK8S", "Deploying MicroK8S")
        self.snap = Snap()
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.preseed_file = preseed_file
        self.accept_defaults = accept_defaults
        self.client = Client()
        self.answer_file = self.tfhelper.path / "addons.auto.tfvars.json"
        self.variables = {}

    def prompt(self, console: Optional[Console] = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = questions.load_answers(self.client, self._CONFIG)
        self.variables.setdefault("addons", {})

        if self.preseed_file:
            preseed = questions.read_preseed(self.preseed_file)
        else:
            preseed = {}
        microk8s_addons_bank = questions.QuestionBank(
            questions=microk8s_addons_questions(),
            console=console,  # type: ignore
            preseed=preseed.get("addons"),
            previous_answers=self.variables.get("addons", {}),
            accept_defaults=self.accept_defaults,
        )
        # Microk8s configuration
        # Let microk8s handle dns server configuration
        self.variables["addons"]["dns"] = ""
        self.variables["addons"]["metallb"] = microk8s_addons_bank.metallb.ask()
        self.variables["addons"]["hostpath-storage"] = ""

        LOG.debug(self.variables)
        questions.write_answers(self.client, self._CONFIG, self.variables)

        # Check if there are any proxies set and add containerd_env to the variables
        http_proxy = self.snap.config.get("proxy.http")
        https_proxy = self.snap.config.get("proxy.https")
        no_proxy = self.snap.config.get("proxy.no")
        if http_proxy or https_proxy:
            self.variables["containerd_env"] = CONTAINERD_ENV_TEMPLATE.format(
                http_proxy=http_proxy, https_proxy=https_proxy, no_proxy=no_proxy
            )

        # Write answers to terraform location as a separate variables file
        self.tfhelper.write_tfvars(self.variables, self.answer_file)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy microk8s"""
        machine_ids = []
        try:
            application = run_sync(self.jhelper.get_application(APPLICATION, MODEL))
            machine_ids.extend(unit.machine.id for unit in application.units)
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))

        self.tfhelper.write_tfvars({"machine_ids": machine_ids})
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        # Note(gboutry): application is in state unknown when it's deployed
        # without units
        try:
            run_sync(
                self.jhelper.wait_application_ready(
                    APPLICATION,
                    MODEL,
                    accepted_status=["active", "unknown"],
                    timeout=MICROK8S_APP_TIMEOUT,
                )
            )
        except TimeoutException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddMicrok8sUnitStep(BaseStep, JujuStepHelper):
    def __init__(self, name: str, jhelper: JujuHelper):
        super().__init__("Add MicroK8S unit", "Adding MicroK8S unit to machine")

        self.name = name
        self.jhelper = jhelper
        self.machine_id = ""

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        client = Client()
        try:
            node = client.cluster.get_node_info(self.name)
            self.machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException as e:
            return Result(ResultType.FAILED, str(e))

        try:
            application = run_sync(self.jhelper.get_application(APPLICATION, MODEL))
        except ApplicationNotFoundException:
            return Result(ResultType.FAILED, "MicroK8S has not been deployed")

        for unit in application.units:
            if unit.machine.id == self.machine_id:
                LOG.debug(
                    (
                        f"Unit {unit.name} is already deployed"
                        f" on machine: {self.machine_id}"
                    )
                )
                return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Add unit to microk8s application on Juju model."""
        try:
            unit = run_sync(
                self.jhelper.add_unit(APPLICATION, MODEL, str(self.machine_id))
            )
            run_sync(
                self.jhelper.wait_unit_ready(
                    unit.name, MODEL, timeout=MICROK8S_UNIT_TIMEOUT
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RemoveMicrok8sUnitStep(BaseStep, JujuStepHelper):
    def __init__(self, name: str, jhelper: JujuHelper):
        super().__init__("Remove MicroK8S unit", "Removing MicroK8S unit from machine")

        self.name = name
        self.jhelper = jhelper
        self.unit = None

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        client = Client()
        try:
            node = client.cluster.get_node_info(self.name)
            machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException:
            LOG.debug(f"Machine {self.name} does not exist, skipping.")
            return Result(ResultType.SKIPPED)

        try:
            application = run_sync(self.jhelper.get_application(APPLICATION, MODEL))
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))
            return Result(ResultType.SKIPPED, "MicroK8S has not been deployed yet")

        for unit in application.units:
            if unit.machine.id == machine_id:
                LOG.debug(f"Unit {unit.name} is deployed on machine: {machine_id}")
                self.unit = unit.name
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Remove unit from microk8s application on Juju model."""
        try:
            run_sync(self.jhelper.remove_unit(APPLICATION, str(self.unit), MODEL))
            run_sync(
                self.jhelper.wait_application_ready(
                    APPLICATION,
                    MODEL,
                    accepted_status=["active", "unknown"],
                    timeout=MICROK8S_UNIT_TIMEOUT,
                )
            )
        except (ApplicationNotFoundException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class AddMicrok8sCloudStep(BaseStep, JujuStepHelper):
    _CONFIG = CONFIG_KEY

    def __init__(self, jhelper: JujuHelper):
        super().__init__(
            "Add MicroK8S cloud", "Adding MicroK8S cloud to Juju controller"
        )

        self.name = MICROK8S_CLOUD
        self.jhelper = jhelper
        self.credential_name = f"{MICROK8S_CLOUD}{CREDENTIAL_SUFFIX}"

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        clouds = run_sync(self.jhelper.get_clouds())
        LOG.debug(f"Clouds registered in the controller: {clouds}")
        # TODO(hemanth): Need to check if cloud credentials are also created?
        if f"cloud-{self.name}" in clouds.keys():
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Add microk8s clouds to Juju controller."""
        try:
            kubeconfig = read_config(Client(), self._CONFIG)
            run_sync(
                self.jhelper.add_k8s_cloud(self.name, self.credential_name, kubeconfig)
            )
        except ConfigItemNotFoundException as e:
            LOG.debug("Failed to add k8s cloud to Juju controller", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class StoreMicrok8sConfigStep(BaseStep, JujuStepHelper):
    _CONFIG = CONFIG_KEY

    def __init__(self, jhelper: JujuHelper):
        super().__init__(
            "Store MicroK8S config",
            "Storing MicroK8S configuration in sunbeam database",
        )
        self.jhelper = jhelper

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            read_config(Client(), self._CONFIG)
        except ConfigItemNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Store MicroK8S config in clusterd."""
        try:
            unit = run_sync(self.jhelper.get_leader_unit(APPLICATION, MODEL))
            result = run_sync(self.jhelper.run_action(unit, MODEL, "kubeconfig"))
            if not result.get("content"):
                return Result(
                    ResultType.FAILED,
                    "ERROR: Failed to retrieve kubeconfig",
                )
            kubeconfig = yaml.safe_load(result["content"])
            update_config(Client(), self._CONFIG, kubeconfig)
        except (
            ApplicationNotFoundException,
            LeaderNotFoundException,
            ActionFailedException,
        ) as e:
            LOG.debug("Failed to store microk8s config", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
