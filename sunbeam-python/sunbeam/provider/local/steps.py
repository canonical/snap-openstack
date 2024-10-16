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
import typing
from functools import cache
from typing import Any, TextIO

from rich.console import Console
from rich.prompt import InvalidResponse, PromptBase

import sunbeam.core.questions
from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.commands.configure import (
    CLOUD_CONFIG_SECTION,
    SetHypervisorUnitsOptionsStep,
)
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.steps import hypervisor
from sunbeam.steps.cluster_status import ClusterStatusStep

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

    def __call__(self, *, default: Any = ..., stream: TextIO | None = None) -> Any:
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


class NicQuestion(sunbeam.core.questions.Question[str]):
    """Ask the user a simple yes / no question."""

    @property
    def question_function(self):
        """Override the question function to use the NicPrompt."""
        return NicPrompt.ask


def local_hypervisor_questions():
    return {
        "nic": NicQuestion("External network's interface"),
    }


class LocalSetHypervisorUnitsOptionsStep(SetHypervisorUnitsOptionsStep):
    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        join_mode: bool = False,
        manifest: Manifest | None = None,
    ):
        super().__init__(
            client,
            [name],
            jhelper,
            model,
            manifest,
            "Apply local hypervisor settings",
            "Applying local hypervisor settings",
        )
        self.join_mode = join_mode

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    def prompt_for_nic(self) -> str | None:
        """Prompt user for nic to use and do some validation."""
        local_hypervisor_bank = sunbeam.core.questions.QuestionBank(
            questions=local_hypervisor_questions(),
            console=console,
            accept_defaults=False,
        )
        nic = None
        while True:
            nic = typing.cast(NicQuestion, local_hypervisor_bank.nic).ask()
            if not nic:
                continue
            if utils.is_configured(nic):
                agree_nic_up = sunbeam.core.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is configured. Any "
                    "configuration will be lost, are you sure you want to "
                    "continue?"
                ).ask()
                if not agree_nic_up:
                    continue
            if utils.is_nic_up(nic) and not utils.is_nic_connected(nic):
                agree_nic_no_link = sunbeam.core.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is not connected. Are "
                    "you sure you want to continue?"
                ).ask()
                if not agree_nic_no_link:
                    continue
            break
        return nic

    def prompt(self, console: Console | None = None) -> None:
        """Determines if the step can take input from the user."""
        # If adding a node before configure step has run then answers will
        # not be populated yet.
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        remote_access_location = self.variables.get("user", {}).get(
            "remote_access_location"
        )
        # If adding new nodes to the cluster then local access makes no sense
        # so always prompt for the nic.
        preseed = {}
        if self.manifest and (
            ext_network := self.manifest.core.config.external_network
        ):
            preseed = ext_network.model_dump(by_alias=True)

        if self.join_mode or remote_access_location == utils.REMOTE_ACCESS:
            # If nic is in the preseed assume the user knows what they are doing and
            # bypass validation
            if nic := preseed.get("nic"):
                self.nics[self.names[0]] = nic
            else:
                self.nics[self.names[0]] = self.prompt_for_nic()


class LocalClusterStatusStep(ClusterStatusStep):
    def models(self) -> list[str]:
        """List of models to query status from."""
        return [self.deployment.openstack_machines_model]

    @cache
    def _has_storage(self) -> bool:
        """Check if deployment has storage."""
        return (
            len(self.deployment.get_client().cluster.list_nodes_by_role("storage")) > 0
        )

    def map_application_status(self, application: str, status: str) -> str:
        """Callback to map application status to a column.

        This callback is called for every unit status with the name of its application.
        """
        if application == hypervisor.APPLICATION:
            if status == "waiting" and not self._has_storage():
                return "active"
        return status

    def _update_microcluster_status(self, status: dict, microcluster_status: dict):
        """How to update microcluster status in the status dict."""
        for member, member_status in microcluster_status.items():
            for node_status in status[
                self.deployment.openstack_machines_model
            ].values():
                if node_status.get("name") != member:
                    continue
                node_status["clusterd-status"] = member_status
