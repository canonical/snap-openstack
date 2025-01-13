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
from typing import Tuple

import click
from rich.console import Console

from sunbeam.core.checks import Check, run_preflight_checks
from sunbeam.core.common import (
    BaseStep,
    ResultType,
    get_step_message,
    get_step_result,
    run_plan,
    validate_nodes,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.provider.local.steps import LocalClusterStatusStep
from sunbeam.provider.maas.steps import MaasClusterStatusStep
from sunbeam.provider.maintenance import checks
from sunbeam.steps.cluster_status import ClusterStatusStep
from sunbeam.steps.hypervisor import EnableHypervisorStep
from sunbeam.steps.maintenance import (
    RunWatcherHostMaintenanceStep,
    RunWatcherWorkloadBalancingStep,
)
from sunbeam.utils import click_option_show_hints

console = Console()
LOG = logging.getLogger(__name__)


def _get_node_roles(
    deployment: Deployment,
    jhelper: JujuHelper,
    console: Console,
    show_hints: bool,
    nodes: list[str],
) -> Tuple[list[str], list[str], list[str]]:
    cluster_status_step: type[ClusterStatusStep]
    if deployment.type == "local":
        cluster_status_step = LocalClusterStatusStep
    else:
        cluster_status_step = MaasClusterStatusStep

    results = run_plan([cluster_status_step(deployment, jhelper)], console, show_hints)
    cluster_status = get_step_message(results, cluster_status_step)

    control_nodes = []
    compute_nodes = []
    storage_nodes = []

    for _, node in cluster_status["controller"].items():
        if node["hostname"] in nodes:
            if "control" in node["status"]:
                control_nodes.append(node["hostname"])
            if "compute" in node["status"]:
                compute_nodes.append(node["hostname"])
            if "storage" in node["status"]:
                storage_nodes.append(node["hostname"])
    return control_nodes, compute_nodes, storage_nodes


@click.command()
@click.option(
    "--nodes",
    help="Nodes to enable into maintenance mode",
    type=click.STRING,
    required=True,
    callback=validate_nodes,
)
@click.option(
    "--force",
    help="Force to ignore preflight_checks",
    type=click.BOOL,
    default=False,
)
@click_option_show_hints
@click.pass_context
def enable_maintenance(
    ctx: click.Context,
    nodes,
    force,
    show_hints: bool = False,
) -> None:
    console.print("Enable maintenance")
    deployment: Deployment = ctx.obj
    jhelper = JujuHelper(deployment.get_connected_controller())

    control_nodes, compute_nodes, _ = _get_node_roles(
        deployment=deployment,
        jhelper=jhelper,
        console=console,
        show_hints=show_hints,
        nodes=nodes,
    )

    # Run preflight_checks
    preflight_checks: list[Check] = []
    # Prefligh checks for control role
    preflight_checks += [
        # This check is to avoid issue which maintenance mode haven't support
        # control role, which should be removed after control role be supported.
        checks.NodeisNotControlRoleCheck(nodes=control_nodes, force=force),
    ]
    # Prefligh checks for compute role
    preflight_checks += [
        checks.InstancesStatusCheck(jhelper=jhelper, nodes=compute_nodes, force=force),
        checks.NoEphemeralDiskCheck(jhelper=jhelper, nodes=compute_nodes, force=force),
    ]
    run_preflight_checks(preflight_checks, console)

    for node in nodes:
        plan: list[BaseStep] = []
        post_checks: list[Check] = []

        if node in compute_nodes:
            plan.append(RunWatcherHostMaintenanceStep(deployment=deployment, node=node))

        results = run_plan(plan, console, show_hints)

        # Run post checks
        if node in compute_nodes:
            # Run post checks only enable maintenance step is completed.
            result = get_step_result(results, RunWatcherHostMaintenanceStep)
            if result.result_type == ResultType.COMPLETED:
                post_checks += [
                    checks.NovaInDisableStatusCheck(
                        jhelper=jhelper, node=node, force=force
                    ),
                    checks.NoInstancesOnNodeCheck(
                        jhelper=jhelper, node=node, force=force
                    ),
                ]
        run_preflight_checks(post_checks, console)
    console.print("Finish enable maintenance")


@click.command()
@click.option(
    "--nodes",
    help="Nodes to enable into maintenance mode",
    type=click.STRING,
    required=True,
    callback=validate_nodes,
)
@click_option_show_hints
@click.pass_context
def disable_maintenance(
    ctx: click.Context,
    nodes,
    show_hints: bool = False,
) -> None:
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.get_connected_controller())

    _, compute_nodes, _ = _get_node_roles(
        deployment=deployment,
        jhelper=jhelper,
        console=console,
        show_hints=show_hints,
        nodes=nodes,
    )

    for node in nodes:
        plan: list[BaseStep] = []
        if node in compute_nodes:
            plan += [
                EnableHypervisorStep(
                    client=client,
                    node=node,
                    jhelper=jhelper,
                    model=deployment.openstack_machines_model,
                ),
                RunWatcherWorkloadBalancingStep(deployment=deployment),
            ]
        run_plan(plan, console, show_hints)
