# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import abc
import functools
import logging
from typing import Any

import click
from rich.console import Console

from sunbeam.core.checks import Check, JujuLoginCheck, run_preflight_checks
from sunbeam.core.common import (
    BaseStep,
    get_step_message,
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.features.maintenance import checks
from sunbeam.features.maintenance.resolve import ResolvedNode
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
from sunbeam.steps.microovn import EnableMicroOVNStep
from sunbeam.utils import click_option_show_hints, pass_method_obj

console = Console()
LOG = logging.getLogger(__name__)


class CommandCancelledError(Exception):
    """Command cancelled error."""


class MaintenanceCommand(abc.ABC):
    """Base class for any maintenance mode command.

    The maintenance mode command should follow check-apply-verify pattern for consistent
    behaviors. This base class only provides the overall pattern.

    Check: Run the pre-flight checks before running any the core commands.
    Apply: Run the core commands related to maintenance mode operations.
    Verify: Run the verification steps to ensure that the cloud reaches expected state.
    """

    @abc.abstractmethod
    def check(self, console: Console) -> None:
        """Run pre-flight checks."""

    @abc.abstractmethod
    def apply(self, console: Console, show_hints: bool, plan_results: dict) -> None:
        """Run the core commands."""

    @abc.abstractmethod
    def verify(self, console: Console) -> None:
        """Run verification steps."""

    @abc.abstractmethod
    def dry_run(self, console: Console, show_hints: bool) -> dict:
        """Dry run command steps."""

    def __call__(self, console: Console, show_hints: bool, dry_run: bool) -> None:
        """Run the commands following check-apply-verify order."""
        try:
            self.check(console)
        except click.ClickException as e:
            err_message = e.message
            help_message = (
                "Pre-flight checks failed, please consult the documentation to"
                " understand what are the pre-flight checks and how to address"
                " those failures before enabling maintenance mode:"
                " https://canonical-openstack.readthedocs-hosted.com/en/latest"
                "/explanation/maintenance-mode/"
            )
            raise click.ClickException(f"{err_message}\n{help_message}") from e

        plan_results = self.dry_run(console, show_hints)
        if dry_run:
            return

        try:
            self.apply(console, show_hints, plan_results)
        except CommandCancelledError as e:
            console.print(str(e))
        except click.ClickException as e:
            raise e
        else:
            self.verify(console)


class EnableMaintenance(MaintenanceCommand):
    """Command to enable maintenance mode."""

    def __init__(
        self,
        resolved: ResolvedNode,
        deployment: Deployment,
        cluster_status: dict[str, Any],
        force: bool = False,
        stop_osds: bool = False,
        allow_downtime: bool = False,
        enable_ceph_crush_rebalancing: bool = False,
        disable_live_migration: bool = False,
        disable_cold_migration: bool = False,
    ):
        self.resolved = resolved
        self.deployment = deployment
        self.cluster_status = cluster_status
        self.force = force
        self.stop_osds = stop_osds
        self.allow_downtime = allow_downtime
        self.enable_ceph_crush_rebalancing = enable_ceph_crush_rebalancing
        self.disable_live_migration = disable_live_migration
        self.disable_cold_migration = disable_cold_migration

        self.model = deployment.openstack_machines_model
        self.client = deployment.get_client()
        self.jhelper = JujuHelper(deployment.juju_controller)
        self.hypervisor_name: str | None = None

    @functools.cached_property
    def ops_viewer(self) -> OperationViewer:
        """Operation viewer, resolved lazily so unknown nodes fail in check()."""
        return OperationViewer(self.resolved.hostname, OperationGoal.EnableMaintenance)

    def _get_hypervisor_name(self) -> str | None:
        """Resolve the nova hypervisor name, or skip the nova steps under --force.

        A node nova does not know about can not be checked; --force skips
        the nova checks and watcher steps instead of failing. check()
        resolves this before anything is applied; later phases reuse the
        cached resolution.
        """
        if self.hypervisor_name is None:
            try:
                self.hypervisor_name = self.resolved.hypervisor_name
            except click.ClickException as e:
                if not self.force:
                    raise
                LOG.warning("Ignoring: %s (--force)", e.message)
        return self.hypervisor_name

    def check(self, console: Console) -> None:
        """Run pre-flight checks."""
        node_status = self.cluster_status.get(self.resolved.hostname, "")

        preflight_checks: list[Check] = [
            checks.NoLastNodeCheck(self.cluster_status, force=self.force),
        ]

        if (
            "compute" in node_status
            and (hypervisor_name := self._get_hypervisor_name()) is not None
        ):
            preflight_checks += [
                checks.WatcherApplicationExistsCheck(self.jhelper),
                checks.InstancesStatusCheck(
                    jhelper=self.jhelper,
                    deployment=self.deployment,
                    node=hypervisor_name,
                    force=self.force,
                ),
                checks.NoEphemeralDiskCheck(
                    jhelper=self.jhelper,
                    deployment=self.deployment,
                    node=hypervisor_name,
                    force=self.force,
                ),
            ]

        if "storage" in node_status:
            preflight_checks += [
                checks.MicroCephMaintenancePreflightCheck(
                    client=self.client,
                    jhelper=self.jhelper,
                    node=self.resolved.clusterd_name,
                    model=self.model,
                    force=self.force,
                    action_params={
                        "name": self.resolved.hostname,
                        "stop-osds": self.stop_osds,
                        "set-noout": not self.enable_ceph_crush_rebalancing,
                    },
                )
            ]

        if "control" in node_status:
            preflight_checks += [
                checks.NoLastControlRoleCheck(
                    self.deployment,
                    self.cluster_status,
                    force=self.force,
                ),
                checks.K8sDqliteRedundancyCheck(
                    self.resolved.clusterd_name,
                    self.jhelper,
                    self.deployment,
                    force=self.force,
                ),
                checks.NoJujuControllerPodCheck(
                    self.resolved.k8s_name,
                    self.deployment,
                ),
                checks.ReplicasRedundancyCheck(
                    self.resolved.k8s_name,
                    self.deployment,
                    force=self.allow_downtime,
                ),
            ]

        run_preflight_checks(preflight_checks, console)

    def apply(self, console: Console, show_hints: bool, plan_results: dict) -> None:
        """Run the core commands."""
        node_status = self.cluster_status.get(self.resolved.hostname, "")

        confirm = self.ops_viewer.prompt()
        if not confirm:
            raise CommandCancelledError("Operation Cancelled!")

        operation_plan: list[BaseStep] = []

        if "compute" in node_status and self._get_hypervisor_name() is not None:
            audit_info = get_step_message(
                plan_results, CreateWatcherHostMaintenanceAuditStep
            )
            operation_plan.append(
                RunWatcherAuditStep(
                    deployment=self.deployment,
                    node=self.resolved.hostname,
                    audit=audit_info["audit"],
                )
            )

        if "storage" in node_status:
            operation_plan.append(
                MicroCephActionStep(
                    client=self.client,
                    node=self.resolved.clusterd_name,
                    jhelper=self.jhelper,
                    model=self.model,
                    action_name="enter-maintenance",
                    action_params={
                        "name": self.resolved.hostname,
                        "set-noout": not self.enable_ceph_crush_rebalancing,
                        "stop-osds": self.stop_osds,
                        "dry-run": False,
                        "ignore-check": True,
                    },
                )
            )

        if "control" in node_status:
            operation_plan += [
                CordonControlRoleNodeStep(
                    self.resolved.clusterd_name,
                    self.client,
                    self.jhelper,
                    self.model,
                    dry_run=False,
                ),
                DrainControlRoleNodeStep(
                    self.resolved.clusterd_name,
                    self.client,
                    self.jhelper,
                    self.model,
                    dry_run=False,
                ),
            ]

        operation_plan_results = run_plan(operation_plan, console, show_hints, True)

        self.ops_viewer.check_operation_succeeded(operation_plan_results)

    def verify(self, console: Console) -> None:
        """Run verification steps."""
        node_status = self.cluster_status.get(self.resolved.hostname, "")

        post_checks: list[Check] = []

        if (
            "compute" in node_status
            and (hypervisor_name := self._get_hypervisor_name()) is not None
        ):
            post_checks += [
                checks.NovaInDisableStatusCheck(
                    jhelper=self.jhelper,
                    deployment=self.deployment,
                    node=hypervisor_name,
                    force=self.force,
                ),
                checks.NoInstancesOnNodeCheck(
                    jhelper=self.jhelper,
                    deployment=self.deployment,
                    node=hypervisor_name,
                    force=self.force,
                ),
            ]

        if "control" in node_status:
            post_checks += [
                checks.ControlRoleNodeCordonedCheck(
                    self.resolved.k8s_name, self.deployment, force=self.force
                ),
            ]

        run_preflight_checks(post_checks, console)

        console.print(f"Enable maintenance for node: {self.resolved.hostname}")

    def dry_run(self, console: Console, show_hints: bool) -> dict:
        """Dry run command steps."""
        node_status = self.cluster_status.get(self.resolved.hostname, "")

        generate_operation_plan: list[BaseStep] = []

        if (
            "compute" in node_status
            and (hypervisor_name := self._get_hypervisor_name()) is not None
        ):
            generate_operation_plan.append(
                CreateWatcherHostMaintenanceAuditStep(
                    deployment=self.deployment,
                    node=hypervisor_name,
                    disable_live_migration=self.disable_live_migration,
                    disable_cold_migration=self.disable_cold_migration,
                )
            )

        if "storage" in node_status:
            generate_operation_plan.append(
                MicroCephActionStep(
                    client=self.client,
                    node=self.resolved.clusterd_name,
                    jhelper=self.jhelper,
                    model=self.model,
                    action_name="enter-maintenance",
                    action_params={
                        "name": self.resolved.hostname,
                        "set-noout": not self.enable_ceph_crush_rebalancing,
                        "stop-osds": self.stop_osds,
                        "dry-run": True,
                        "ignore-check": True,
                    },
                )
            )

        if "control" in node_status:
            generate_operation_plan += [
                CordonControlRoleNodeStep(
                    self.resolved.clusterd_name,
                    self.client,
                    self.jhelper,
                    self.model,
                    dry_run=True,
                ),
                DrainControlRoleNodeStep(
                    self.resolved.clusterd_name,
                    self.client,
                    self.jhelper,
                    self.model,
                    dry_run=True,
                ),
            ]

        generate_operation_plan_results = run_plan(
            generate_operation_plan, console, show_hints
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

        if "compute" in node_status and self._get_hypervisor_name() is not None:
            audit_info = get_step_message(
                generate_operation_plan_results, CreateWatcherHostMaintenanceAuditStep
            )
            self.ops_viewer.add_watch_actions(actions=audit_info["actions"])
        if "storage" in node_status:
            self.ops_viewer.add_maintenance_action_steps(
                action_result=microceph_enter_maintenance_dry_run_action_result
            )
        if "control" in node_status:
            self.ops_viewer.add_cordon_control_role_step(
                result=cordon_k8s_node_dry_run_result
            )
            self.ops_viewer.add_drain_control_role_step(
                result=drain_k8s_node_dry_run_result
            )

        console.print(self.ops_viewer.dry_run_message)

        return generate_operation_plan_results


class DisableMaintenance(MaintenanceCommand):
    """Command to disable maintenance mode."""

    def __init__(
        self,
        resolved: ResolvedNode,
        deployment: Deployment,
        cluster_status: dict[str, Any],
        disable_instance_rebalancing: bool = False,
    ):
        self.resolved = resolved
        self.deployment = deployment
        self.cluster_status = cluster_status
        self.disable_instance_rebalancing = disable_instance_rebalancing

        self.model = deployment.openstack_machines_model
        self.client = deployment.get_client()
        self.jhelper = JujuHelper(deployment.juju_controller)

    @functools.cached_property
    def ops_viewer(self) -> OperationViewer:
        """Operation viewer, resolved lazily so unknown nodes fail in check()."""
        return OperationViewer(self.resolved.hostname, OperationGoal.DisableMaintenance)

    def check(self, console: Console) -> None:
        """Run pre-flight checks."""
        node_status = self.cluster_status.get(self.resolved.hostname, "")

        # Resolve the names the node's roles need up front: verify() runs
        # after the uncordon is applied, so an unresolvable name must fail
        # here, before any change is made.
        if (
            "compute" in node_status
            or "storage" in node_status
            or "control" in node_status
        ):
            self.resolved.clusterd_name
        if "control" in node_status:
            self.resolved.k8s_name

        # Run preflight_checks
        preflight_checks: list[Check] = []

        if "compute" in node_status:
            preflight_checks += [
                checks.WatcherApplicationExistsCheck(jhelper=self.jhelper),
            ]

        run_preflight_checks(preflight_checks, console)

    def apply(self, console: Console, show_hints: bool, plan_results: dict) -> None:
        """Run the core commands."""
        node_status = self.cluster_status.get(self.resolved.hostname, "")

        confirm = self.ops_viewer.prompt()
        if not confirm:
            raise CommandCancelledError("Operation Cancelled!")

        operation_plan: list[BaseStep] = []
        if "compute" in node_status:
            operation_plan += [
                EnableHypervisorStep(
                    client=self.client,
                    node=self.resolved.clusterd_name,
                    jhelper=self.jhelper,
                    model=self.model,
                ),
            ]
            if not self.disable_instance_rebalancing:
                audit_info = get_step_message(
                    plan_results,
                    CreateWatcherWorkloadBalancingAuditStep,
                )
                operation_plan += [
                    RunWatcherAuditStep(
                        deployment=self.deployment,
                        node=self.resolved.hostname,
                        audit=audit_info["audit"],
                    ),
                ]
        if "storage" in node_status:
            operation_plan.append(
                MicroCephActionStep(
                    client=self.client,
                    node=self.resolved.clusterd_name,
                    jhelper=self.jhelper,
                    model=self.model,
                    action_name="exit-maintenance",
                    action_params={
                        "name": self.resolved.hostname,
                        "dry-run": False,
                        "ignore-check": True,
                    },
                )
            )
        if "control" in node_status:
            operation_plan.append(
                UncordonControlRoleNodeStep(
                    self.resolved.clusterd_name,
                    self.client,
                    self.jhelper,
                    self.model,
                    dry_run=False,
                )
            )

        operation_plan_results = run_plan(operation_plan, console, show_hints, True)
        self.ops_viewer.check_operation_succeeded(operation_plan_results)

    def verify(self, console: Console) -> None:
        """Run verification steps."""
        node_status = self.cluster_status.get(self.resolved.hostname, "")

        post_checks: list[Check] = []

        if "control" in node_status:
            post_checks += [
                checks.ControlRoleNodeUncordonedCheck(
                    self.resolved.k8s_name,
                    self.deployment,
                    force=False,
                ),
            ]

        run_preflight_checks(post_checks, console)

        console.print(f"Disable maintenance for node: {self.resolved.hostname}")

    def dry_run(self, console: Console, show_hints: bool) -> dict:
        """Dry run command steps."""
        node_status = self.cluster_status.get(self.resolved.hostname, "")

        generate_operation_plan: list[BaseStep] = []

        if "compute" in node_status:
            if not self.disable_instance_rebalancing:
                generate_operation_plan.append(
                    CreateWatcherWorkloadBalancingAuditStep(
                        deployment=self.deployment,
                    )
                )
        if "storage" in node_status:
            generate_operation_plan.append(
                MicroCephActionStep(
                    client=self.client,
                    node=self.resolved.clusterd_name,
                    jhelper=self.jhelper,
                    model=self.model,
                    action_name="exit-maintenance",
                    action_params={
                        "name": self.resolved.hostname,
                        "dry-run": True,
                        "ignore-check": True,
                    },
                )
            )
        if "control" in node_status:
            generate_operation_plan.append(
                UncordonControlRoleNodeStep(
                    self.resolved.clusterd_name,
                    self.client,
                    self.jhelper,
                    self.model,
                    dry_run=True,
                )
            )

        generate_operation_plan_results = run_plan(
            generate_operation_plan, console, show_hints
        )

        if not self.disable_instance_rebalancing:
            audit_info = get_step_message(
                generate_operation_plan_results, CreateWatcherWorkloadBalancingAuditStep
            )
        microceph_exit_maintenance_dry_run_action_result = get_step_message(
            generate_operation_plan_results, MicroCephActionStep
        )
        uncordon_k8s_node_dry_run_result = get_step_message(
            generate_operation_plan_results, UncordonControlRoleNodeStep
        )

        if "compute" in node_status:
            self.ops_viewer.add_step(step_name=EnableHypervisorStep.__name__)
            if not self.disable_instance_rebalancing:
                self.ops_viewer.add_watch_actions(actions=audit_info["actions"])
        if "storage" in node_status:
            self.ops_viewer.add_maintenance_action_steps(
                action_result=microceph_exit_maintenance_dry_run_action_result
            )
        if "control" in node_status:
            self.ops_viewer.add_uncordon_control_role_step(
                result=uncordon_k8s_node_dry_run_result
            )
        if "network" in node_status:
            self.ops_viewer.add_step(step_name=EnableMicroOVNStep.__name__)

        console.print(self.ops_viewer.dry_run_message)

        return generate_operation_plan_results


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
@click.option(
    "--allow-downtime",
    help=(
        "Optional to drain the workload that has less than or equal to 1 replica"
        " even though it might cause service downtime."
        " Defaults to not allow service down time when"
        " entering maintenance mode."
    ),
    is_flag=True,
    default=False,
)
@click.option(
    "--disable-migration",
    help=(
        "Disable migration during maintenance (both, live, cold)."
        " If not specified, defaults to Watcher's defaults (both False)"
        " If specified without value, defaults to 'both' (both True)"
    ),
    type=click.Choice(["both", "live", "cold"]),
    default=None,
    is_flag=False,
    flag_value="both",
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
    allow_downtime,
    disable_migration,
    show_hints: bool = False,
) -> None:
    """Enable maintenance mode for node."""
    # Login to the Juju controller
    run_preflight_checks([JujuLoginCheck(deployment.juju_account)], console)

    jhelper = JujuHelper(deployment.juju_controller)
    cluster_status = get_cluster_status(
        deployment=deployment,
        jhelper=jhelper,
        console=console,
        show_hints=show_hints,
    )

    # Default to Watcher's defaults (both False) when not specified
    if not disable_migration:
        disable_live_migration = False
        disable_cold_migration = False
    else:
        disable_live_migration = disable_migration in ["both", "live"]
        disable_cold_migration = disable_migration in ["both", "cold"]

    enable_maintenance = EnableMaintenance(
        ResolvedNode(node, deployment, cluster_status, jhelper),
        deployment,
        cluster_status,
        force=force,
        stop_osds=stop_osds,
        allow_downtime=allow_downtime,
        enable_ceph_crush_rebalancing=enable_ceph_crush_rebalancing,
        disable_live_migration=disable_live_migration,
        disable_cold_migration=disable_cold_migration,
    )

    enable_maintenance(console, show_hints, dry_run)


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
    # Login to the Juju controller
    run_preflight_checks([JujuLoginCheck(deployment.juju_account)], console)

    jhelper = JujuHelper(deployment.juju_controller)
    cluster_status = get_cluster_status(
        deployment=deployment,
        jhelper=jhelper,
        console=console,
        show_hints=show_hints,
    )

    disable_maintenance = DisableMaintenance(
        ResolvedNode(node, deployment, cluster_status, jhelper),
        deployment,
        cluster_status,
        disable_instance_rebalancing=disable_instance_workload_rebalancing,
    )

    disable_maintenance(console, show_hints, dry_run)
