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
import shutil
from pathlib import Path
from typing import Optional

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.common import BaseStep, Result, ResultType, run_plan
from sunbeam.jobs.juju import JujuHelper
from sunbeam.plugins.interface.v1.base import PluginRequirement
from sunbeam.plugins.interface.v1.openstack import (
    OpenStackControlPlanePlugin,
    TerraformPlanLocation,
)

LOG = logging.getLogger(__name__)
console = Console()


class CaasConfigureStep(BaseStep):
    """Configure CaaS service."""

    def __init__(
        self,
        tfhelper: TerraformHelper,
    ):
        super().__init__(
            "Configure Container as a Service",
            "Configure Cloud for Container as a Service use",
        )
        self.tfhelper = tfhelper

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error configuring Container as a Service plugin.")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class CaasPlugin(OpenStackControlPlanePlugin):
    version = Version("0.0.1")
    requires = {
        PluginRequirement("secrets"),
        PluginRequirement("orchestration"),
        PluginRequirement("loadbalancer", optional=True),
    }

    def __init__(self, client: Client) -> None:
        super().__init__(
            "caas",
            client,
            tf_plan_location=TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO,
        )
        self.configure_plan = "caas-setup"

    def set_application_names(self) -> list:
        """Application names handled by the terraform plan."""
        apps = ["magnum", "magnum-mysql-router"]
        if self.get_database_topology() == "multi":
            apps.extend(["magnum-mysql"])

        return apps

    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "magnum-channel": "2023.2/edge",
            "enable-magnum": True,
            **self.add_horizon_plugin_to_tfvars("magnum"),
        }

    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-magnum": False,
            **self.remove_horizon_plugin_from_tfvars("magnum"),
        }

    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    @click.command()
    def enable_plugin(self) -> None:
        """Enable Container as a Service plugin."""
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable Container as a Service plugin."""
        super().disable_plugin()

    @click.command()
    def configure(self):
        """Configure Cloud for Container as a Service use."""
        src = Path(__file__).parent / "etc" / self.configure_plan
        dst = self.snap.paths.user_common / "etc" / self.configure_plan
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

        data_location = self.snap.paths.user_data
        jhelper = JujuHelper(self.client, data_location)
        admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / self.configure_plan,
            env=admin_credentials,
            plan="caas-plan",
            backend="http",
            data_location=data_location,
        )
        plan = [
            TerraformInitStep(tfhelper),
            CaasConfigureStep(tfhelper),
        ]

        run_plan(plan, console)

    def commands(self) -> dict:
        """Dict of clickgroup along with commands."""
        commands = super().commands()
        try:
            enabled = self.enabled
        except ClusterServiceUnavailableException:
            LOG.debug(
                "Failed to query for plugin status, is cloud bootstrapped ?",
                exc_info=True,
            )
            enabled = False

        if enabled:
            commands.update(
                {
                    "configure": [{"name": "caas", "command": self.configure}],
                }
            )
        return commands
