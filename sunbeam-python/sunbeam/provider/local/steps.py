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

import logging
from typing import Any, Optional, TextIO

from rich.console import Console
from rich.prompt import InvalidResponse, PromptBase

import sunbeam.jobs.questions
from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.commands.cluster_status import ClusterStatusStep
from sunbeam.commands.configure import (
    CLOUD_CONFIG_SECTION,
    SetHypervisorUnitsOptionsStep,
)
from sunbeam.jobs.juju import JujuHelper

LOG = logging.getLogger(__name__)
console = Console()


class NicPrompt(PromptBase[str]):
    """A prompt that asks for a NIC on the local machine and validates it.

    Unlike other questions this prompt validates the users choice and if it
    fails validation the user has an oppertunity to fix any issue in another
    session and continue without exiting from the prompt.
    """

    response_type = str
    validate_error_message = "[prompt.invalid]Please valid nic"

    def check_choice(self, value: str) -> bool:
        """Validate the choice of nic."""
        nics = utils.get_free_nics(include_configured=True)
        try:
            value = value.strip().lower()
        except AttributeError:
            # Likely an empty string has been returned.
            raise InvalidResponse(f"\n'{value}' not a valid nic name")
        if value not in nics:
            raise InvalidResponse(f"\n'{value}' not found")
        return True

    def __call__(self, *, default: Any = ..., stream: Optional[TextIO] = None) -> Any:
        """Run the prompt loop.

        Args:
            default (Any, optional): Optional default value.
            stream (TextIO, optional): Optional stream to write to.

        Returns:
            PromptType: Processed value.
        """
        while True:
            # Limit options displayed to user to unconfigured nics.
            self.choices = utils.get_free_nics(include_configured=False)
            # Assume that if a default has been passed in and it is configured it is
            # probably the right one. The user will be prompted to confirm later.
            if not default or default not in utils.get_free_nics(
                include_configured=True
            ):
                if len(self.choices) > 0:
                    default = self.choices[0]
            self.pre_prompt()
            prompt = self.make_prompt(default)
            value = self.get_input(self.console, prompt, password=False, stream=stream)
            if value == "":
                if default:
                    # Unlike super.__call__ do not return here as we still need to
                    # validate the choice.
                    value = default
                else:
                    self.console.print("\nInvalid nic")
                    continue
            try:
                return_value = self.process_response(value)
            except InvalidResponse as error:
                self.on_validate_error(value, error)
                continue
            else:
                return return_value


class NicQuestion(sunbeam.jobs.questions.Question):
    """Ask the user a simple yes / no question."""

    @property
    def question_function(self):
        """Override the question function to use the NicPrompt."""
        return NicPrompt.ask


def local_hypervisor_questions():
    return {
        "nic": NicQuestion(
            "Free network interface that will be configured for external traffic"
        ),
    }


class LocalSetHypervisorUnitsOptionsStep(SetHypervisorUnitsOptionsStep):
    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        join_mode: bool = False,
        deployment_preseed: dict | None = None,
    ):
        super().__init__(
            client,
            [name],
            jhelper,
            model,
            deployment_preseed or {},
            "Apply local hypervisor settings",
            "Applying local hypervisor settings",
        )
        self.join_mode = join_mode

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    def prompt_for_nic(self) -> None:
        """Prompt user for nic to use and do some validation."""
        local_hypervisor_bank = sunbeam.jobs.questions.QuestionBank(
            questions=local_hypervisor_questions(),
            console=console,
            accept_defaults=False,
        )
        nic = None
        while True:
            nic = local_hypervisor_bank.nic.ask()
            if utils.is_configured(nic):
                agree_nic_up = sunbeam.jobs.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is configured. Any "
                    "configuration will be lost, are you sure you want to "
                    "continue?"
                ).ask()
                if not agree_nic_up:
                    continue
            if utils.is_nic_up(nic) and not utils.is_nic_connected(nic):
                agree_nic_no_link = sunbeam.jobs.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is not connected. Are "
                    "you sure you want to continue?"
                ).ask()
                if not agree_nic_no_link:
                    continue
            break
        return nic

    def prompt(self, console: Optional[Console] = None) -> None:
        """Determines if the step can take input from the user."""
        # If adding a node before configure step has run then answers will
        # not be populated yet.
        self.variables = sunbeam.jobs.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        remote_access_location = self.variables.get("user", {}).get(
            "remote_access_location"
        )
        # If adding new nodes to the cluster then local access makes no sense
        # so always prompt for the nic.
        if self.join_mode or remote_access_location == utils.REMOTE_ACCESS:
            ext_net_preseed = self.preseed.get("external_network", {})
            # If nic is in the preseed assume the user knows what they are doing and
            # bypass validation
            if ext_net_preseed.get("nic"):
                self.nics[self.names[0]] = ext_net_preseed.get("nic")
            else:
                self.nics[self.names[0]] = self.prompt_for_nic()


class LocalClusterStatusStep(ClusterStatusStep):
    def models(self) -> list[str]:
        """List of models to query status from."""
        return [self.deployment.infrastructure_model]

    def _update_microcluster_status(self, status: dict, microcluster_status: dict):
        """How to update microcluster status in the status dict."""
        for member, member_status in microcluster_status.items():
            for node_status in status[self.deployment.infrastructure_model].values():
                if node_status.get("name") != member:
                    continue
                node_status["clusterd-status"] = member_status
