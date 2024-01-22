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

from sunbeam.clusterd.client import Client
from sunbeam.commands.terraform import TerraformHelper
from sunbeam.jobs.juju import MODEL, JujuHelper
from sunbeam.jobs.steps import (
    AddMachineUnitStep,
    DeployMachineApplicationStep,
    RemoveMachineUnitStep,
)

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsSunbeamMachine"
APPLICATION = "sunbeam-machine"
SUNBEAM_MACHINE_APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
SUNBEAM_MACHINE_UNIT_TIMEOUT = (
    1200  # 20 minutes, adding / removing units can take a long time
)


class DeploySunbeamMachineApplicationStep(DeployMachineApplicationStep):
    """Deploy openstack-hyervisor application using Terraform cloud"""

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
            "Deploy sunbeam-machine",
            "Deploying Sunbeam Machine",
        )

    def extra_tfvars(self) -> dict:
        return {"machine_model": self.model}

    def get_application_timeout(self) -> int:
        return SUNBEAM_MACHINE_APP_TIMEOUT


class AddSunbeamMachineUnitStep(AddMachineUnitStep):
    """Add Sunbeam machine Unit."""

    def __init__(self, client: Client, name: str, jhelper: JujuHelper):
        super().__init__(
            client,
            name,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            MODEL,
            "Add Sunbeam-machine unit",
            f"Adding Sunbeam Machine unit to machine {name}",
        )

    def get_unit_timeout(self) -> int:
        return SUNBEAM_MACHINE_UNIT_TIMEOUT


class RemoveSunbeamMachineStep(RemoveMachineUnitStep):
    """Remove Sunbeam machine Unit."""

    def __init__(self, client: Client, name: str, jhelper: JujuHelper):
        super().__init__(
            client,
            name,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            MODEL,
            "Remove sunbeam-machine unit",
            f"Removing sunbeam-machine unit from machine {name}",
        )

    def get_unit_timeout(self) -> int:
        return SUNBEAM_MACHINE_UNIT_TIMEOUT
