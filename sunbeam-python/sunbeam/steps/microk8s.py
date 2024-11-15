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

import logging

import yaml
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core import questions
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    update_config,
    validate_cidr_or_ip_ranges,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    JujuStepHelper,
    LeaderNotFoundException,
    run_sync,
)
from sunbeam.core.k8s import (
    LOADBALANCER_QUESTION_DESCRIPTION,
    MICROK8S_KUBECONFIG_KEY,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.steps import (
    AddMachineUnitsStep,
    DeployMachineApplicationStep,
)
from sunbeam.core.terraform import TerraformHelper
from sunbeam.steps.k8s import (
    AddK8SCloudInClientStep,
    AddK8SCloudStep,
    AddK8SCredentialStep,
    CheckMysqlK8SDistributionStep,
    CheckOvnK8SDistributionStep,
    CheckRabbitmqK8SDistributionStep,
    CordonK8SUnitStep,
    DestroyK8SApplicationStep,
    DrainK8SUnitStep,
    MigrateK8SKubeconfigStep,
    RemoveK8SUnitsStep,
    UpdateK8SCloudStep,
)

LOG = logging.getLogger(__name__)
APPLICATION = "microk8s"
MICROK8S_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
MICROK8S_UNIT_TIMEOUT = 1200  # 20 minutes, adding / removing units can take a long time
MICROK8S_CONFIG_KEY = "TerraformVarsMicrok8s"
MICROK8S_ADDONS_CONFIG_KEY = "TerraformVarsMicrok8sAddons"


def microk8s_addons_questions():
    return {
        "metallb": questions.PromptQuestion(
            "OpenStack APIs IP ranges",
            default_value="172.16.1.201-172.16.1.240",
            validation_function=validate_cidr_or_ip_ranges,
            description=LOADBALANCER_QUESTION_DESCRIPTION,
        ),
    }


class DeployMicrok8sApplicationStep(DeployMachineApplicationStep):
    """Deploy Microk8s application using Terraform."""

    _ADDONS_CONFIG = MICROK8S_ADDONS_CONFIG_KEY

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
            MICROK8S_CONFIG_KEY,
            APPLICATION,
            model,
            "Deploy MicroK8S",
            "Deploying MicroK8S",
            refresh,
        )

        self.accept_defaults = accept_defaults
        self.variables: dict = {}

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return MICROK8S_APP_TIMEOUT

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
        self.variables = questions.load_answers(self.client, self._ADDONS_CONFIG)
        self.variables.setdefault("addons", {})

        preseed = {}
        if addons := self.manifest.core.config.addons:
            preseed = addons.model_dump(by_alias=True)

        microk8s_addons_bank = questions.QuestionBank(
            questions=microk8s_addons_questions(),
            console=console,  # type: ignore
            preseed=preseed,
            previous_answers=self.variables.get("addons", {}),
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )
        # Microk8s configuration
        # Let microk8s handle dns server configuration
        self.variables["addons"]["dns"] = ""
        self.variables["addons"]["metallb"] = microk8s_addons_bank.metallb.ask()
        self.variables["addons"]["hostpath-storage"] = ""

        LOG.debug(self.variables)
        questions.write_answers(self.client, self._ADDONS_CONFIG, self.variables)
        # Write answers to terraform location as a separate variables file
        answer_file = self.tfhelper.path / "addons.auto.tfvars.json"
        self.tfhelper.write_tfvars(self.variables, answer_file)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        # No need to prompt for questions in case of refresh
        if self.refresh:
            return False

        return True

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        return {
            "endpoint_bindings": [
                {"space": self.deployment.get_space(Networks.MANAGEMENT)},
            ]
        }


class AddMicrok8sUnitsStep(AddMachineUnitsStep):
    """Add Microk8s Unit."""

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
            MICROK8S_CONFIG_KEY,
            APPLICATION,
            model,
            "Add MicroK8S unit",
            "Adding MicroK8S unit to machine",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return MICROK8S_UNIT_TIMEOUT


class RemoveMicrok8sUnitsStep(RemoveK8SUnitsStep):
    """Remove Microk8s Unit."""

    _APPLICATION = APPLICATION
    _K8S_CONFIG_KEY = MICROK8S_CONFIG_KEY
    _K8S_UNIT_TIMEOUT = MICROK8S_UNIT_TIMEOUT


class AddMicrok8sCloudStep(AddK8SCloudStep):
    _KUBECONFIG = MICROK8S_KUBECONFIG_KEY


class AddMicrok8sCloudInClientStep(AddK8SCloudInClientStep):
    _KUBECONFIG = MICROK8S_KUBECONFIG_KEY


class UpdateMicroK8SCloudStep(UpdateK8SCloudStep):
    _KUBECONFIG = MICROK8S_KUBECONFIG_KEY


class AddMicrok8sCredentialStep(AddK8SCredentialStep):
    _KUBECONFIG = MICROK8S_KUBECONFIG_KEY


class MigrateMicroK8SKubeconfigStep(MigrateK8SKubeconfigStep):
    _SUBSTRATE: str = APPLICATION
    _KUBECONFIG: str = MICROK8S_KUBECONFIG_KEY
    _ACTION: str = "kubeconfig"

    def _extract_action_result(self, action_result: dict) -> str | None:
        return action_result.get("content")


class StoreMicrok8sConfigStep(BaseStep, JujuStepHelper):
    _CONFIG = MICROK8S_KUBECONFIG_KEY

    def __init__(self, client: Client, jhelper: JujuHelper, model: str):
        super().__init__(
            "Store MicroK8S config",
            "Storing MicroK8S configuration in sunbeam database",
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
            read_config(self.client, self._CONFIG)
        except ConfigItemNotFoundException:
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Store MicroK8S config in clusterd."""
        try:
            unit = run_sync(self.jhelper.get_leader_unit(APPLICATION, self.model))
            result = run_sync(self.jhelper.run_action(unit, self.model, "kubeconfig"))
            if not result.get("content"):
                return Result(
                    ResultType.FAILED,
                    "ERROR: Failed to retrieve kubeconfig",
                )
            kubeconfig = yaml.safe_load(result["content"])
            update_config(self.client, self._CONFIG, kubeconfig)
        except (
            ApplicationNotFoundException,
            LeaderNotFoundException,
            ActionFailedException,
        ) as e:
            LOG.debug("Failed to store microk8s config", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class CheckMysqlMicroK8SDistributionStep(CheckMysqlK8SDistributionStep):
    _SUBSTRATE = APPLICATION


class CheckRabbitmqMicroK8SDistributionStep(CheckRabbitmqK8SDistributionStep):
    _SUBSTRATE = APPLICATION


class CheckOvnMicroK8SDistributionStep(CheckOvnK8SDistributionStep):
    _SUBSTRATE = APPLICATION


class CordonMicroK8SUnitStep(CordonK8SUnitStep):
    _SUBSTRATE = APPLICATION


class DrainMicroK8SUnitStep(DrainK8SUnitStep):
    _SUBSTRATE = APPLICATION


class DestroyMicroK8SApplicationStep(DestroyK8SApplicationStep):
    _APPLICATION = APPLICATION
    _CONFIG_KEY = MICROK8S_CONFIG_KEY
