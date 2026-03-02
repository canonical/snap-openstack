# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from packaging.version import Version
from rich.console import Console

from sunbeam.core.ceph import SetCephProviderStep
from sunbeam.core.common import BaseStep, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import FeatureConfig
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.features.interface.v1.base import EnableDisableFeature
from sunbeam.features.microceph.steps import (
    DeployMicrocephApplicationStep,
    DestroyMicrocephApplicationStep,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj

LOG = logging.getLogger(__name__)
console = Console()


class CephFeature(EnableDisableFeature):
    version = Version("0.0.1")

    name = "ceph"

    def run_enable_plans(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ) -> None:
        """Run plans to enable ceph support via microceph."""
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper("microceph-plan")
        jhelper = JujuHelper(deployment.juju_controller)
        manifest = deployment.get_manifest(self.user_manifest)
        plan: list[BaseStep] = [
            SetCephProviderStep(client),
            TerraformInitStep(tfhelper),
            DeployMicrocephApplicationStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            ),
        ]
        run_plan(plan, console, show_hints)
        click.echo("Ceph enabled.")

    def run_disable_plans(self, deployment: Deployment, show_hints: bool) -> None:
        """Run plans to disable ceph support and teardown microceph."""
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper("microceph-plan")
        jhelper = JujuHelper(deployment.juju_controller)
        manifest = deployment.get_manifest()
        plan: list[BaseStep] = [
            SetCephProviderStep(client, no_default_storage=True),
            TerraformInitStep(tfhelper),
            DestroyMicrocephApplicationStep(
                client,
                tfhelper,
                jhelper,
                manifest,
                deployment.openstack_machines_model,
            ),
        ]
        run_plan(plan, console, show_hints)
        click.echo("Ceph disabled.")

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable ceph support."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable ceph support."""
        self.disable_feature(deployment, show_hints)
