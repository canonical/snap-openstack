# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from sunbeam.clusterd.client import Client
from sunbeam.core.common import Role
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.steps import (
    DeployMachineApplicationStep,
    DestroyMachineApplicationStep,
    RemoveMachineUnitsStep,
)
from sunbeam.core.terraform import TerraformHelper

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsSunbeamMachine"
APPLICATION = "sunbeam-machine"
SUNBEAM_MACHINE_APP_TIMEOUT = 1800  # 30 minutes, deploys multiple units in parallel
SUNBEAM_MACHINE_UNIT_TIMEOUT = (
    1800  # 30 minutes, adding / removing units, can be multiple units in parallel
)
SUBORDINATE_APPLICATIONS = ["epa-orchestrator"]


class DeploySunbeamMachineApplicationStep(DeployMachineApplicationStep):
    """Deploy openstack-hyervisor application using Terraform cloud."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        proxy_settings: dict = {},
    ):
        super().__init__(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            CONFIG_KEY,
            APPLICATION,
            model,
            [Role.CONTROL, Role.COMPUTE, Role.STORAGE],
            "Deploy sunbeam-machine",
            "Deploying Sunbeam Machine",
        )
        self.proxy_settings = proxy_settings

    def get_application_timeout(self) -> int:
        """Return application timeout."""
        return SUNBEAM_MACHINE_APP_TIMEOUT

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        return {
            "endpoint_bindings": [
                {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                {
                    "endpoint": "sunbeam-machine",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
            ],
            "charm_config": {
                "http_proxy": self.proxy_settings.get("HTTP_PROXY", ""),
                "https_proxy": self.proxy_settings.get("HTTPS_PROXY", ""),
                "no_proxy": self.proxy_settings.get("NO_PROXY", ""),
            },
        }


class RemoveSunbeamMachineUnitsStep(RemoveMachineUnitsStep):
    """Remove Sunbeam machine Unit."""

    def __init__(
        self, client: Client, names: list[str] | str, jhelper: JujuHelper, model: str
    ):
        super().__init__(
            client,
            names,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            model,
            "Remove sunbeam-machine unit",
            f"Removing sunbeam-machine unit from machine(s) {names}",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return SUNBEAM_MACHINE_UNIT_TIMEOUT


class DestroySunbeamMachineApplicationStep(DestroyMachineApplicationStep):
    """Destroy Sunbeam Machine application using Terraform."""

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
            CONFIG_KEY,
            [*SUBORDINATE_APPLICATIONS, APPLICATION],
            model,
            "Destroy Sunbeam Machine",
            "Destroying Sunbeam Machine",
        )

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return SUNBEAM_MACHINE_APP_TIMEOUT
