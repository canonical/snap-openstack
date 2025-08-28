# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Ubuntu Pro subscription management feature."""

import logging
import re
from pathlib import Path

import click
import pydantic
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper, JujuStepHelper
from sunbeam.core.manifest import (
    FeatureConfig,
    Manifest,
    SoftwareConfig,
    TerraformManifest,
)
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
    TerraformStateLockedException,
)
from sunbeam.features.interface.v1.base import EnableDisableFeature
from sunbeam.utils import (
    click_option_show_hints,
    pass_method_obj,
)

LOG = logging.getLogger(__name__)
console = Console()

APPLICATION = "ubuntu-pro"
APP_TIMEOUT = 300  # 5 minutes, managing the application should be fast
UNIT_TIMEOUT = 1200  # 15 minutes, adding / removing units can take a long time


class ProFeatureConfig(FeatureConfig):
    token: str = pydantic.Field(
        description="Token to attach the Ubuntu Pro subscription."
    )


class EnableUbuntuProApplicationStep(BaseStep, JujuStepHelper):
    """Enable Ubuntu Pro application using Terraform."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        token: str,
        model: str,
    ):
        super().__init__("Enable Ubuntu Pro", "Enabling Ubuntu Pro support")
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.token = token
        self.model = model

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return False

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy ubuntu-pro."""
        extra_tfvars = {"machine-model": self.model, "token": self.token}
        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=None,
                override_tfvars=extra_tfvars,
            )
        except (TerraformException, TerraformStateLockedException) as e:
            return Result(ResultType.FAILED, str(e))

        # Note(gboutry): application is in state unknown when it's deployed
        # without units
        try:
            self.jhelper.wait_application_ready(
                APPLICATION,
                self.model,
                accepted_status=["active", "blocked", "unknown"],
                timeout=APP_TIMEOUT,
            )

            # Check status of pro application for any token issues
            pro_app = self.jhelper.get_application(APPLICATION, self.model)
            if pro_app.app_status.current == "blocked":
                re_match = re.search(
                    ".*stderr:(.*)stdout.*", pro_app.app_status.message, re.DOTALL
                )
                if re_match:
                    message = re_match.group(1)
                else:
                    message = pro_app.app_status.message

                return Result(ResultType.FAILED, message)
        except TimeoutError as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DisableUbuntuProApplicationStep(BaseStep, JujuStepHelper):
    """Disable Ubuntu Pro application using Terraform."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        manifest: Manifest,
    ):
        super().__init__("Disable Ubuntu Pro", "Disabling Ubuntu Pro support")
        self.client = client
        self.tfhelper = tfhelper
        self.manifest = manifest

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return False

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to disable ubuntu-pro."""
        extra_tfvars = {"token": ""}
        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=None,
                override_tfvars=extra_tfvars,
            )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ProFeature(EnableDisableFeature):
    _manifest: Manifest | None
    version = Version("0.0.1")

    name = "pro"

    def __init__(self) -> None:
        super().__init__()
        self.snap = Snap()
        self.tfplan = "ubuntu-pro-plan"
        self.tfplan_dir = f"deploy-{self.name}"
        self._manifest = None

    @property
    def manifest(self) -> Manifest:
        """Return the manifest."""
        if self._manifest:
            return self._manifest

        manifest = click.get_current_context().obj.get_manifest(self.user_manifest)
        self._manifest = manifest

        return manifest

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            terraform={
                self.tfplan: TerraformManifest(
                    source=Path(__file__).parent / "etc" / self.tfplan_dir
                )
            }
        )

    def run_enable_plans(
        self, deployment: Deployment, config: ProFeatureConfig, show_hints: bool
    ):
        """Run the enablement plans."""
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            TerraformInitStep(tfhelper),
            EnableUbuntuProApplicationStep(
                deployment.get_client(),
                tfhelper,
                jhelper,
                self.manifest,
                config.token,
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console, show_hints)

        click.echo(
            "Please check minimum hardware requirements for support:\n\n"
            "    https://canonical-openstack.readthedocs-hosted.com/en/latest/reference/enterprise-requirements/\n"
        )
        click.echo("Ubuntu Pro enabled.")

    def run_disable_plans(self, deployment: Deployment, show_hints: bool):
        """Run the disablement plans."""
        tfhelper = deployment.get_tfhelper(self.tfplan)
        plan = [
            TerraformInitStep(tfhelper),
            DisableUbuntuProApplicationStep(
                deployment.get_client(),
                tfhelper,
                self.manifest,
            ),
        ]

        run_plan(plan, console, show_hints)
        click.echo("Ubuntu Pro disabled.")

    @click.command()
    @pass_method_obj
    @click.argument("token", type=str)
    @click_option_show_hints
    def enable_cmd(self, deployment: Deployment, token: str, show_hints: bool) -> None:
        """Enable Ubuntu Pro across deployment.

        Minimum hardware requirements for support:

        https://canonical-openstack.readthedocs-hosted.com/en/latest/reference/enterprise-requirements/
        """
        self.enable_feature(deployment, ProFeatureConfig(token=token), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Ubuntu Pro across deployment."""
        self.disable_feature(deployment, show_hints)
