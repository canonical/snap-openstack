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

import ast
import logging
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.commands.terraform import TerraformHelper
from sunbeam.jobs import questions
from sunbeam.jobs.common import BaseStep, Result, ResultType
from sunbeam.jobs.juju import (
    MODEL,
    ActionFailedException,
    JujuHelper,
    UnitNotFoundException,
    run_sync,
)
from sunbeam.jobs.steps import (
    AddMachineUnitStep,
    DeployMachineApplicationStep,
    RemoveMachineUnitStep,
)

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsMicrocephPlan"
CONFIG_DISKS_KEY = "TerraformVarsMicroceph"
APPLICATION = "microceph"
MICROCEPH_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
MICROCEPH_UNIT_TIMEOUT = (
    1200  # 15 minutes, adding / removing units can take a long time
)
OSD_PATH_PREFIX = "/dev/disk/by-id/"


def microceph_questions():
    return {
        "osd_devices": questions.PromptQuestion(
            "Disks to attach to MicroCeph",
        ),
    }


class DeployMicrocephApplicationStep(DeployMachineApplicationStep):
    """Deploy Microceph application using Terraform"""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__(
            client,
            tfhelper,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            MODEL,
            "Deploy MicroCeph",
            "Deploying MicroCeph",
        )

    def get_application_timeout(self) -> int:
        return MICROCEPH_APP_TIMEOUT


class AddMicrocephUnitStep(AddMachineUnitStep):
    """Add Microceph Unit."""

    def __init__(self, client: Client, name: str, jhelper: JujuHelper):
        super().__init__(
            client,
            name,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            MODEL,
            "Add MicroCeph unit",
            "Adding MicroCeph unit to machine",
        )

    def get_unit_timeout(self) -> int:
        return MICROCEPH_UNIT_TIMEOUT


class RemoveMicrocephUnitStep(RemoveMachineUnitStep):
    """Remove Microceph Unit."""

    def __init__(self, client: Client, name: str, jhelper: JujuHelper):
        super().__init__(
            client,
            name,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            MODEL,
            "Remove MicroCeph unit",
            "Removing MicroCeph unit from machine",
        )

    def get_unit_timeout(self) -> int:
        return MICROCEPH_UNIT_TIMEOUT


class ConfigureMicrocephOSDStep(BaseStep):
    """Configure Microceph OSD disks"""

    _CONFIG = CONFIG_DISKS_KEY

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        preseed_file: Optional[Path] = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Configure MicroCeph storage", "Configuring MicroCeph storage")
        self.client = client
        self.name = name
        self.jhelper = jhelper
        self.preseed_file = preseed_file
        self.accept_defaults = accept_defaults
        self.variables = {}
        self.machine_id = ""
        self.disks = ""

    def microceph_config_questions(self):
        disks = self.get_unpartitioned_disks()
        disks_str = None
        if len(disks) > 0:
            disks_str = ",".join(disks)

        questions = microceph_questions()
        # Specialise question with local disk information.
        questions["osd_devices"].default_value = disks_str
        return questions

    def get_unpartitioned_disks(self) -> list:
        unpartitioned_disks = []

        try:
            node = self.client.cluster.get_node_info(self.name)
            self.machine_id = str(node.get("machineid"))
            unit = run_sync(
                self.jhelper.get_unit_from_machine(APPLICATION, self.machine_id, MODEL)
            )
            LOG.debug(f"Running action list-disks on {unit.entity_id}")
            action_result = run_sync(
                self.jhelper.run_action(unit.entity_id, MODEL, "list-disks")
            )
            LOG.debug(f"Result after running action list-disks: {action_result}")

            disks = ast.literal_eval(action_result.get("unpartitioned-disks", "[]"))
            unpartitioned_disks = [disk.get("path") for disk in disks]
            # Remove duplicates if any
            unpartitioned_disks = list(set(unpartitioned_disks))
            if OSD_PATH_PREFIX in unpartitioned_disks:
                unpartitioned_disks.remove(OSD_PATH_PREFIX)

        except (UnitNotFoundException, ActionFailedException) as e:
            LOG.debug(str(e))
            raise click.ClickException("Unable to list disks")

        LOG.debug(f"Unpartitioned disks: {unpartitioned_disks}")
        return unpartitioned_disks

    def prompt(self, console: Optional[Console] = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = questions.load_answers(self.client, self._CONFIG)
        self.variables.setdefault("microceph_config", {})
        self.variables["microceph_config"].setdefault(self.name, {"osd_devices": ""})

        if self.preseed_file:
            preseed = questions.read_preseed(self.preseed_file)
        else:
            preseed = {}
        # Set defaults
        preseed.setdefault("microceph_config", {})
        preseed["microceph_config"].setdefault(self.name, {"osd_devices": None})

        # Preseed can have osd_devices as list. If so, change to comma separated str
        osd_devices = preseed.get("microceph_config").get(self.name).get("osd_devices")
        if isinstance(osd_devices, list):
            osd_devices_str = ",".join(osd_devices)
            preseed["microceph_config"][self.name]["osd_devices"] = osd_devices_str

        microceph_config_bank = questions.QuestionBank(
            questions=self.microceph_config_questions(),
            console=console,  # type: ignore
            preseed=preseed.get("microceph_config").get(self.name),
            previous_answers=self.variables.get("microceph_config").get(self.name),
            accept_defaults=self.accept_defaults,
        )
        # Microceph configuration
        self.disks = microceph_config_bank.osd_devices.ask()
        self.variables["microceph_config"][self.name]["osd_devices"] = self.disks

        LOG.debug(self.variables)
        questions.write_answers(self.client, self._CONFIG, self.variables)

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
        if not self.disks:
            LOG.debug(
                "Skipping ConfigureMicrocephOSDStep as no osd devices are selected"
            )
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Configure local disks on microceph."""
        try:
            unit = run_sync(
                self.jhelper.get_unit_from_machine(APPLICATION, self.machine_id, MODEL)
            )
            LOG.debug(f"Running action add-osd on {unit.entity_id}")
            action_result = run_sync(
                self.jhelper.run_action(
                    unit.entity_id,
                    MODEL,
                    "add-osd",
                    action_params={
                        "device-id": self.disks,
                    },
                )
            )
            LOG.debug(f"Result after running action add-osd: {action_result}")
        except (UnitNotFoundException, ActionFailedException) as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
