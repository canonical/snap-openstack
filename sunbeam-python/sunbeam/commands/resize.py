# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from click.core import ParameterSource
from rich.console import Console

from sunbeam.clusterd.client import Client
from sunbeam.core.common import click_option_topology, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.steps.cinder_volume import DeployCinderVolumeApplicationStep
from sunbeam.steps.k8s import PatchCoreDNSStep
from sunbeam.steps.microceph import (
    DeployMicrocephApplicationStep,
    SetCephMgrPoolSizeStep,
)
from sunbeam.steps.openstack import DeployControlPlaneStep
from sunbeam.utils import click_option_show_hints

LOG = logging.getLogger(__name__)
console = Console()


@click.command()
@click_option_topology
@click.option(
    "-f",
    "--force",
    help=(
        "Force resizing to incompatible topology. "
        "This option is deprecated and the value is ignored."
    ),
    is_flag=True,
)
@click_option_show_hints
@click.pass_context
def resize(
    ctx: click.Context, topology: str, show_hints: bool, force: bool = False
) -> None:
    """Expand the control plane to fit available nodes."""
    deployment: Deployment = ctx.obj
    client: Client = deployment.get_client()
    manifest = deployment.get_manifest()

    openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
    microceph_tfhelper = deployment.get_tfhelper("microceph-plan")
    cinder_volume_tfhelper = deployment.get_tfhelper("cinder-volume-plan")
    jhelper = JujuHelper(deployment.juju_controller)

    storage_nodes = client.cluster.list_nodes_by_role("storage")

    parameter_source = click.get_current_context().get_parameter_source("force")
    if parameter_source == ParameterSource.COMMANDLINE:
        LOG.warning("WARNING: Option --force is deprecated and the value is ignored.")

    plan = []
    if len(storage_nodes):
        # Change default-pool-size based on number of storage nodes
        plan.extend(
            [
                TerraformInitStep(microceph_tfhelper),
                DeployMicrocephApplicationStep(
                    deployment,
                    client,
                    microceph_tfhelper,
                    jhelper,
                    manifest,
                    deployment.openstack_machines_model,
                ),
                SetCephMgrPoolSizeStep(
                    client,
                    jhelper,
                    deployment.openstack_machines_model,
                ),
            ]
        )

    plan.extend(
        [
            PatchCoreDNSStep(deployment, jhelper),
            TerraformInitStep(openstack_tfhelper),
            DeployControlPlaneStep(
                deployment,
                openstack_tfhelper,
                jhelper,
                manifest,
                topology,
                deployment.openstack_machines_model,
            ),
        ]
    )

    if len(storage_nodes):
        # DeployCinderVolumeApplicationStep depends on openstack-tfhelper
        # to get outputs, so let OpenStack deployment complete first
        plan.extend(
            [
                TerraformInitStep(cinder_volume_tfhelper),
                DeployCinderVolumeApplicationStep(
                    deployment,
                    client,
                    cinder_volume_tfhelper,
                    jhelper,
                    manifest,
                    deployment.openstack_machines_model,
                ),
            ]
        )

    run_plan(plan, console, show_hints)

    click.echo("Resize complete.")
