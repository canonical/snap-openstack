# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from rich.console import Console

from sunbeam.core.checks import Check, run_preflight_checks
from sunbeam.core.common import (
    BaseStep,
    get_step_message,
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.features.maintenance import checks
from sunbeam.features.maintenance.utils import (
    OperationGoal,
    OperationViewer,
    get_cluster_status,
)

from sunbeam.steps.hypervisor import EnableHypervisorStep
from sunbeam.steps.maintenance import (
    CordonControlRoleNodeStep,
    CreateWatcherHostMaintenanceAuditStep,
    CreateWatcherWorkloadBalancingAuditStep,
    DrainControlRoleNodeStep,
    MicroCephActionStep,
    RunWatcherAuditStep,
    UncordonControlRoleNodeStep,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj

console = Console()
LOG = logging.getLogger(__name__)


@click.command()
@click.argument(
    "node",
    type=click.STRING,
)
@click.option(
    "--force",
    help="Force to ignore preflight checks",
    is_flag=True,
    default=False,
)
@click.option(
    "--dry-run",
    help="Show required operation steps to put node into maintenance mode",
    is_flag=True,
    default=False,
)
@click.option(
    "--enable-ceph-crush-rebalancing",
    help="Enable CRUSH automatically rebalancing in the ceph cluster",
    is_flag=True,
    default=False,
)
@click.option(
    "--stop-osds",
    help=(
        "Optional to stop and disable OSD service on that node."
        " Defaults to keep the OSD service running when"
        " entering maintenance mode"
    ),
    is_flag=True,
    default=False,
)
@click_option_show_hints
@pass_method_obj
def enable(
    cls,
    deployment: Deployment,
    node,
    force,
    dry_run,
    enable_ceph_crush_rebalancing,
    stop_osds,
    show_hints: bool = False,
) -> None:
    """Enable maintenance mode for node."""
    jhelper = JujuHelper(deployment.juju_controller)

    cluster_status = get_cluster_status(
        deployment=deployment,
        jhelper=jhelper,
        console=console,
        show_hints=show_hints,
    )
    node_status = cluster_status.get(node)

    if not node_status:
        raise click.ClickException(f"Node: {node} does not exist in cluster")

    # Run preflight_checks
    preflight_checks: list[Check] = [
        checks.NoLastNodeCheck(cluster_status, force=force)
    ]

    if "compute" in node_status:
        preflight_checks += [
            checks.WatcherApplicationExistsCheck(jhelper=jhelper),
            checks.InstancesStatusCheck(jhelper=jhelper, node=node, force=force),
            checks.NoEphemeralDiskCheck(jhelper=jhelper, node=node, force=force),
        ]
    if "storage" in node_status:
        preflight_checks += [
            checks.MicroCephMaintenancePreflightCheck(
                client=deployment.get_client(),
                jhelper=jhelper,
                node=node,
                model=deployment.openstack_machines_model,
                force=force,
                action_params={
                    "name": node,
                    "set-noout": not enable_ceph_crush_rebalancing,
                    "stop-osds": stop_osds,
                },
            )
        ]
    if "control" in node_status:
        preflight_checks += [
            checks.NoLastControlRoleCheck(cluster_status, force=force),
            checks.ControlRoleRedundancyCheck(
                node,
                jhelper,
                deployment,
                force=force,
            ),
            checks.JujuContollerPodCheck(node, deployment, force=force),
            checks.ControlRoleNodeDrainCheck(node, deployment, force=force),
        ]

    run_preflight_checks(preflight_checks, console)

    # Generate operations
    generate_operation_plan: list[BaseStep] = []
    if "compute" in node_status:
        generate_operation_plan.append(
            CreateWatcherHostMaintenanceAuditStep(deployment=deployment, node=node)
        )
    if "storage" in node_status:
        generate_operation_plan.append(
            MicroCephActionStep(
                client=deployment.get_client(),
                node=node,
                jhelper=jhelper,
                model=deployment.openstack_machines_model,
                action_name="enter-maintenance",
                action_params={
                    "name": node,
                    "set-noout": not enable_ceph_crush_rebalancing,
                    "stop-osds": stop_osds,
                    "dry-run": True,
                    "ignore-check": True,
                },
            )
        )
    if "control" in node_status:
        # The order is important
        generate_operation_plan += [
            CordonControlRoleNodeStep(
                node,
                deployment.get_client(),
                jhelper,
                deployment.openstack_machines_model,
                dry_run=True,
            ),
            DrainControlRoleNodeStep(
                node,
                deployment.get_client(),
                jhelper,
                deployment.openstack_machines_model,
                dry_run=True,
            ),
        ]

    generate_operation_plan_results = run_plan(
        generate_operation_plan, console, show_hints
    )

    audit_info = get_step_message(
        generate_operation_plan_results, CreateWatcherHostMaintenanceAuditStep
    )
    microceph_enter_maintenance_dry_run_action_result = get_step_message(
        generate_operation_plan_results, MicroCephActionStep
    )
    drain_k8s_node_dry_run_result = get_step_message(
        generate_operation_plan_results, DrainControlRoleNodeStep
    )
    cordon_k8s_node_dry_run_result = get_step_message(
        generate_operation_plan_results, CordonControlRoleNodeStep
    )

    ops_viewer = OperationViewer(node, OperationGoal.EnableMaintenance)
    if "compute" in node_status:
        ops_viewer.add_watch_actions(actions=audit_info["actions"])
    if "storage" in node_status:
        ops_viewer.add_maintenance_action_steps(
            action_result=microceph_enter_maintenance_dry_run_action_result
        )
    if "control" in node_status:
        ops_viewer.add_drain_control_role_step(result=drain_k8s_node_dry_run_result)
        ops_viewer.add_cordon_control_role_step(result=cordon_k8s_node_dry_run_result)

    if dry_run:
        console.print(ops_viewer.dry_run_message)
        return

    confirm = ops_viewer.prompt()
    if not confirm:
        return

    # Run operations
    operation_plan: list[BaseStep] = []
    if "compute" in node_status:
        operation_plan.append(
            RunWatcherAuditStep(
                deployment=deployment, node=node, audit=audit_info["audit"]
            )
        )
    if "storage" in node_status:
        operation_plan.append(
            MicroCephActionStep(
                client=deployment.get_client(),
                node=node,
                jhelper=jhelper,
                model=deployment.openstack_machines_model,
                action_name="enter-maintenance",
                action_params={
                    "name": node,
                    "set-noout": not enable_ceph_crush_rebalancing,
                    "stop-osds": stop_osds,
                    "dry-run": False,
                    "ignore-check": True,
                },
            )
        )
    if "control" in node_status:
        operation_plan += [
            CordonControlRoleNodeStep(
                node,
                deployment.get_client(),
                jhelper,
                deployment.openstack_machines_model,
                dry_run=False,
            ),
            DrainControlRoleNodeStep(
                node,
                deployment.get_client(),
                jhelper,
                deployment.openstack_machines_model,
                dry_run=False,
            ),
        ]

    operation_plan_results = run_plan(operation_plan, console, show_hints, True)
    ops_viewer.check_operation_succeeded(operation_plan_results)

    # Run post checks
    post_checks: list[Check] = []
    if "compute" in node_status:
        post_checks += [
            checks.NovaInDisableStatusCheck(jhelper=jhelper, node=node, force=force),
            checks.NoInstancesOnNodeCheck(jhelper=jhelper, node=node, force=force),
        ]
    if "control" in node_status:
        post_checks += [
            checks.ControlRoleNodeDrainedCheck(node, deployment, force=force),
            checks.ControlRoleNodeCordonedCheck(node, deployment, force=force),
        ]

    run_preflight_checks(post_checks, console)
    console.print(f"Enable maintenance for node: {node}")


@click.command()
@click.argument(
    "node",
    type=click.STRING,
)
@click.option(
    "--dry-run",
    help="Show required operation steps to put node out of maintenance mode",
    default=False,
    is_flag=True,
)
@click.option(
    "--disable-instance-workload-rebalancing",
    help="Disable instance workload rebalancing during exit maintenance mode",
    default=False,
    is_flag=True,
)
@click_option_show_hints
@pass_method_obj
def disable(
    cls,
    deployment,
    disable_instance_workload_rebalancing,
    dry_run,
    node,
    show_hints: bool = False,
) -> None:
    """Disable maintenance mode for node."""
    jhelper = JujuHelper(deployment.juju_controller)

    cluster_status = get_cluster_status(
        deployment=deployment,
        jhelper=jhelper,
        console=console,
        show_hints=show_hints,
    )
    node_status = cluster_status.get(node)

    if not node_status:
        raise click.ClickException(f"Node: {node} does not exist in node_status")

    # Run preflight_checks
    preflight_checks: list[Check] = []

    if "compute" in node_status:
        preflight_checks += [
            checks.WatcherApplicationExistsCheck(jhelper=jhelper),
        ]

    run_preflight_checks(preflight_checks, console)

    generate_operation_plan: list[BaseStep] = []
    if "compute" in node_status:
        if not disable_instance_workload_rebalancing:
            generate_operation_plan.append(
                CreateWatcherWorkloadBalancingAuditStep(
                    deployment=deployment, node=node
                )
            )
    if "storage" in node_status:
        generate_operation_plan.append(
            MicroCephActionStep(
                client=deployment.get_client(),
                node=node,
                jhelper=jhelper,
                model=deployment.openstack_machines_model,
                action_name="exit-maintenance",
                action_params={
                    "name": node,
                    "dry-run": True,
                    "ignore-check": True,
                },
            )
        )
    if "control" in node_status:
        generate_operation_plan.append(
            UncordonControlRoleNodeStep(
                node,
                deployment.get_client(),
                jhelper,
                deployment.openstack_machines_model,
                dry_run=True,
            )
        )

    generate_operation_plan_results = run_plan(
        generate_operation_plan, console, show_hints
    )

    if not disable_instance_workload_rebalancing:
        audit_info = get_step_message(
            generate_operation_plan_results, CreateWatcherWorkloadBalancingAuditStep
        )
    microceph_exit_maintenance_dry_run_action_result = get_step_message(
        generate_operation_plan_results, MicroCephActionStep
    )
    uncordon_k8s_node_dry_run_result = get_step_message(
        generate_operation_plan_results, UncordonControlRoleNodeStep
    )

    ops_viewer = OperationViewer(node, OperationGoal.DisableMaintenance)
    if "compute" in node_status:
        ops_viewer.add_step(step_name=EnableHypervisorStep.__name__)
        if not disable_instance_workload_rebalancing:
            ops_viewer.add_watch_actions(actions=audit_info["actions"])
    if "storage" in node_status:
        ops_viewer.add_maintenance_action_steps(
            action_result=microceph_exit_maintenance_dry_run_action_result
        )
    if "control" in node_status:
        ops_viewer.add_uncordon_control_role_step(
            result=uncordon_k8s_node_dry_run_result
        )

    if dry_run:
        console.print(ops_viewer.dry_run_message)
        return

    confirm = ops_viewer.prompt()
    if not confirm:
        return

    operation_plan: list[BaseStep] = []
    if "compute" in node_status:
        operation_plan += [
            EnableHypervisorStep(
                client=deployment.get_client(),
                node=node,
                jhelper=jhelper,
                model=deployment.openstack_machines_model,
            ),
        ]
        if not disable_instance_workload_rebalancing:
            operation_plan += [
                RunWatcherAuditStep(
                    deployment=deployment, node=node, audit=audit_info["audit"]
                ),
            ]
    if "storage" in node_status:
        operation_plan.append(
            MicroCephActionStep(
                client=deployment.get_client(),
                node=node,
                jhelper=jhelper,
                model=deployment.openstack_machines_model,
                action_name="exit-maintenance",
                action_params={
                    "name": node,
                    "dry-run": False,
                    "ignore-check": True,
                },
            )
        )
    if "control" in node_status:
        operation_plan.append(
            UncordonControlRoleNodeStep(
                node,
                deployment.get_client(),
                jhelper,
                deployment.openstack_machines_model,
                dry_run=False,
            )
        )

    operation_plan_results = run_plan(operation_plan, console, show_hints, True)
    ops_viewer.check_operation_succeeded(operation_plan_results)

    console.print(f"Disable maintenance for node: {node}")
