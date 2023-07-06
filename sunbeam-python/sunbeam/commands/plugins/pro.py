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

"""Ubuntu Pro subscription management plugin."""

import logging
import shutil
from typing import Optional

import click
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.common import BaseStep, Result, ResultType, run_plan
from sunbeam.jobs.juju import MODEL, JujuHelper, TimeoutException, run_sync

LOG = logging.getLogger(__name__)
console = Console()

APPLICATION = "ubuntu-pro"
APP_TIMEOUT = 180  # 3 minutes, managing the application should be fast
UNIT_TIMEOUT = 1200  # 15 minutes, adding / removing units can take a long time


class EnableUbuntuProApplicationStep(BaseStep, JujuStepHelper):
    """Enable Ubuntu Pro application using Terraform"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        token: str,
    ):
        super().__init__("Enable Ubuntu Pro", "Enabling Ubuntu Pro support")
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.token = token

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return False

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy ubuntu-pro"""
        self.tfhelper.write_tfvars({"token": self.token})
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
                    accepted_status=["active", "blocked", "unknown"],
                    timeout=APP_TIMEOUT,
                )
            )

            # Check status of pro application for any token issues
            pro_app = run_sync(
                self.jhelper.get_application(
                    APPLICATION,
                    MODEL,
                )
            )
            if pro_app.status == "blocked":
                message = "unknown error"
                for unit in pro_app.units:
                    if "invalid token" in unit.workload_status_message:
                        message = "invalid token"
                LOG.warning(f"Unable to enable Ubuntu Pro: {message}")
                return Result(ResultType.FAILED, message)
        except TimeoutException as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DisableUbuntuProApplicationStep(BaseStep, JujuStepHelper):
    """Disable Ubuntu Pro application using Terraform"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
    ):
        super().__init__("Disable Ubuntu Pro", "Disabling Ubuntu Pro support")
        self.tfhelper = tfhelper

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return False

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to disable ubuntu-pro"""
        self.tfhelper.write_tfvars({"token": ""})
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


@click.command()
@click.option(
    "-t",
    "--token",
    help="Ubuntu Pro token to use for subscription attachment",
    prompt=True,
)
def enable_pro(token: str) -> None:
    """Enable Ubuntu Pro across deployment.

    Minimum hardware requirements for support:

    https://microstack.run/docs/enterprise-reqs
    """

    tfplan = "deploy-ubuntu-pro"
    snap = Snap()
    src = snap.paths.snap / "etc" / tfplan
    dst = snap.paths.user_common / "etc" / tfplan
    LOG.debug(f"Updating {dst} from {src}...")
    shutil.copytree(src, dst, dirs_exist_ok=True)

    data_location = snap.paths.user_data
    tfhelper = TerraformHelper(
        path=snap.paths.user_common / "etc" / tfplan,
        plan="ubuntu-pro-plan",
        backend="http",
        data_location=data_location,
    )
    jhelper = JujuHelper(data_location)
    plan = [
        TerraformInitStep(tfhelper),
        EnableUbuntuProApplicationStep(tfhelper, jhelper, token),
    ]

    run_plan(plan, console)

    click.echo(
        "Please check minimum hardware requirements for support:\n\n"
        "    https://microstack.run/docs/enterprise-reqs\n"
    )
    click.echo("Ubuntu Pro enabled.")


@click.command()
def disable_pro() -> None:
    """Disable Ubuntu Pro across deployment."""

    tfplan = "deploy-ubuntu-pro"
    snap = Snap()
    src = snap.paths.snap / "etc" / tfplan
    dst = snap.paths.user_common / "etc" / tfplan
    LOG.debug(f"Updating {dst} from {src}...")
    shutil.copytree(src, dst, dirs_exist_ok=True)

    data_location = snap.paths.user_data
    tfhelper = TerraformHelper(
        path=snap.paths.user_common / "etc" / tfplan,
        plan="ubuntu-pro-plan",
        backend="http",
        data_location=data_location,
    )
    plan = [
        TerraformInitStep(tfhelper),
        DisableUbuntuProApplicationStep(tfhelper),
    ]

    run_plan(plan, console)

    click.echo("Ubuntu Pro disabled.")


def register(enable: click.Group, disable: click.Group):
    """Register plugin enable and disable commands."""
    enable.add_command(enable_pro, "pro")
    disable.add_command(disable_pro, "pro")
