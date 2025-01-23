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
from os import linesep
from typing import Any

import click
from rich.console import Console
from watcherclient import v1 as watcher

from sunbeam.core.checks import Check, run_preflight_checks
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    get_step_message,
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.questions import ConfirmQuestion, Question
from sunbeam.provider.local.steps import LocalClusterStatusStep
from sunbeam.provider.maas.steps import MaasClusterStatusStep
from sunbeam.provider.maintenance import checks
from sunbeam.steps.cluster_status import ClusterStatusStep
from sunbeam.steps.hypervisor import EnableHypervisorStep
from sunbeam.steps.maintenance import (
    CreateWatcherHostMaintenanceAuditStep,
    CreateWatcherWorkloadBalancingAuditStep,
    MicroCephActionStep,
    RunWatcherAuditStep,
)
from sunbeam.utils import click_option_show_hints

console = Console()
LOG = logging.getLogger(__name__)


def _get_node_status(
    deployment: Deployment,
    jhelper: JujuHelper,
    console: Console,
    show_hints: bool,
    node: str,
) -> dict[str, Any]:
    cluster_status_step: type[ClusterStatusStep]
    if deployment.type == "local":
        cluster_status_step = LocalClusterStatusStep
    else:
        cluster_status_step = MaasClusterStatusStep

    results = run_plan([cluster_status_step(deployment, jhelper)], console, show_hints)
    cluster_status = get_step_message(results, cluster_status_step)

    for _, _node in cluster_status["openstack-machines"].items():
        if _node["hostname"] == node:
            return _node["status"]
    return {}


class OperationViewer:
    def __init__(self, node: str):
        self.node = node
        self.operations: list[str] = []
        self.operation_states: dict[str, str] = {}

    @staticmethod
    def _get_watcher_action_key(action: watcher.Action) -> str:
        key: str
        if action.action_type == "change_nova_service_state":
            key = "{} state={} resource={}".format(
                action.action_type,
                action.input_parameters["state"],
                action.input_parameters["resource_name"],
            )
        if action.action_type == "migrate":
            key = "Migrate instance type={} resource={}".format(
                action.input_parameters["migration_type"],
                action.input_parameters["resource_name"],
            )
        return key

    def add_watch_actions(self, actions: list[watcher.Action]):
        """Append Watcher actions to operations."""
        for action in actions:
            key = self._get_watcher_action_key(action)
            self.operations.append(key)
            self.operation_states[key] = "PENDING"

    def add_maintenance_action_steps(self, action_result: dict[str, Any]):
        """Append juju maintenance action's actions to operations."""
        for step, action in action_result.get("actions", {}).items():
            self.operations.append(action["id"])
            self.operation_states[action["id"]] = "SKIPPED"

    def add_step(self, step_name: str):
        """Append BaseStep to operations."""
        self.operations.append(step_name)
        self.operation_states[step_name] = "SKIPPED"

    def update_watcher_actions_result(self, actions: list[watcher.Action]):
        """Update result of Watcher actions."""
        for action in actions:
            key = self._get_watcher_action_key(action)
            self.operation_states[key] = action.state

    def update_maintenance_action_steps_result(self, action_result: dict[str, Any]):
        """Update result of juju maintenance action's actions."""
        for _, action in action_result.get("actions", {}).items():
            status = "SUCCEEDED"
            if action.get("error"):
                status = "FAILED"
            self.operation_states[action["id"]] = status

    def update_step_result(self, step_name: str, result: Result):
        """Update BaseStep's result."""
        if result.result_type == ResultType.COMPLETED:
            self.operation_states[step_name] = "SUCCEEDED"
        elif result.result_type == ResultType.FAILED:
            self.operation_states[step_name] = "FAILED"
        else:
            self.operation_states[step_name] = "SKIPPED"

    def _operation_plan(self) -> str:
        msg = ""
        for idx, step in enumerate(self.operations):
            msg += f"\t{idx}: {step}{linesep}"
        return msg

    def dry_run_message(self) -> str:
        """Return CLI output message for dry-run."""
        return (
            f"Required operations to put {self.node} into "
            f"maintenance mode:{linesep}{self._operation_plan()}"
        )

    def prompt(self) -> bool:
        """Determines if the operations is confirmed by the user."""
        question: Question = ConfirmQuestion(
            f"Continue to run operations to put {self.node} into"
            f" maintenance mode:{linesep}{self._operation_plan()}"
        )
        return question.ask() or False

    def _operation_result(self) -> str:
        msg = ""
        for idx, step in enumerate(self.operations):
            msg += f"\t{idx}: {step} {self.operation_states[step]}{linesep}"
        if msg:
            msg = f"Operation result:{linesep}" + msg
        return msg

    def check_operation_succeeded(self, results: dict[str, Result]):
        """Check if all the operations are succeeded."""
        if any(
            result.result_type == ResultType.FAILED for _, result in results.items()
        ):
            failed_result: Result
            for name, result in results.items():
                if result.result_type == ResultType.FAILED:
                    failed_result = result
                if name == RunWatcherAuditStep.__name__:
                    self.update_watcher_actions_result(result.message)
                elif name == MicroCephActionStep.__name__:
                    self.update_maintenance_action_steps_result(result.message)
                elif name == EnableHypervisorStep.__name__:
                    self.update_step_result(name, result)
            if failed_result:
                raise click.ClickException(result.message)


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
    "--set-noout",
    help="prevent CRUSH from automatically rebalancing the ceph cluster",
    is_flag=True,
    default=True,
)
@click.option(
    "--stop-osds",
    help=(
        "Optional to stop and disable OSD service on that node."
        " Defaults to keep the OSD service running when"
        " entering maintenance mode"
    ),
    is_flag=True,
    default=True,
)
@click_option_show_hints
@click.pass_context
def enable_maintenance(
    ctx: click.Context,
    node,
    force,
    dry_run,
    set_noout,
    stop_osds,
    show_hints: bool = False,
) -> None:
    console.print(f"Enable maintenance for {node}")
    deployment: Deployment = ctx.obj
    jhelper = JujuHelper(deployment.get_connected_controller())

    node_status = _get_node_status(
        deployment=deployment,
        jhelper=jhelper,
        console=console,
        show_hints=show_hints,
        node=node,
    )

    # This check is to avoid issue which maintenance mode haven't support
    # control role, which should be removed after control role be supported.
    if "control" in node_status:
        msg = f"Node {node} is control role, which doesn't support maintenance mode"
        if force:
            LOG.warning(f"Ignore issue: {msg}")
        else:
            raise click.ClickException(msg)

    # Run preflight_checks
    preflight_checks: list[Check] = []
    # Preflight checks for compute role
    if "compute" in node_status:
        preflight_checks += [
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
                    "set-noout": set_noout,
                    "stop-osds": stop_osds,
                },
            )
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
                    "set-noout": set_noout,
                    "stop-osds": stop_osds,
                    "dry-run": True,
                    "ignore-check": True,
                },
            )
        )

    generate_operation_plan_results = run_plan(
        generate_operation_plan, console, show_hints
    )

    audit_info = get_step_message(
        generate_operation_plan_results, CreateWatcherHostMaintenanceAuditStep
    )
    microceph_enter_maintenance_dry_run_action_result = get_step_message(
        generate_operation_plan_results, MicroCephActionStep
    )

    ops_viewer = OperationViewer(node)
    if "compute" in node_status:
        ops_viewer.add_watch_actions(actions=audit_info["actions"])
    if "storage" in node_status:
        ops_viewer.add_maintenance_action_steps(
            action_result=microceph_enter_maintenance_dry_run_action_result
        )

    if dry_run:
        console.print(ops_viewer.dry_run_message())
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
                    "set-noout": set_noout,
                    "stop-osds": stop_osds,
                    "dry-run": False,
                    "ignore-check": True,
                },
            )
        )

    operation_plan_results = run_plan(operation_plan, console, show_hints, True)
    for name, result in operation_plan_results.items():
        console.print(f"{name}: {result.message}")
    ops_viewer.check_operation_succeeded(operation_plan_results)

    # Run post checks
    post_checks: list[Check] = []
    if "compute" in node_status:
        post_checks += [
            checks.NovaInDisableStatusCheck(jhelper=jhelper, node=node, force=force),
            checks.NoInstancesOnNodeCheck(jhelper=jhelper, node=node, force=force),
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
    help="Show required operation steps to put node into maintenance mode",
    default=False,
    is_flag=True,
)
@click_option_show_hints
@click.pass_context
def disable_maintenance(
    ctx: click.Context,
    dry_run,
    node,
    show_hints: bool = False,
) -> None:
    deployment: Deployment = ctx.obj
    jhelper = JujuHelper(deployment.get_connected_controller())

    node_status = _get_node_status(
        deployment=deployment,
        jhelper=jhelper,
        console=console,
        show_hints=show_hints,
        node=node,
    )

    generate_operation_plan: list[BaseStep] = []
    if "compute" in node_status:
        generate_operation_plan.append(
            CreateWatcherWorkloadBalancingAuditStep(deployment=deployment, node=node)
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

    generate_operation_plan_results = run_plan(
        generate_operation_plan, console, show_hints
    )

    audit_info = get_step_message(
        generate_operation_plan_results, CreateWatcherWorkloadBalancingAuditStep
    )
    microceph_exit_maintenance_dry_run_action_result = get_step_message(
        generate_operation_plan_results, MicroCephActionStep
    )

    ops_viewer = OperationViewer(node)
    if "compute" in node_status:
        ops_viewer.add_step(step_name=EnableHypervisorStep.__name__)
        ops_viewer.add_watch_actions(actions=audit_info["actions"])
    if "storage" in node_status:
        ops_viewer.add_maintenance_action_steps(
            action_result=microceph_exit_maintenance_dry_run_action_result
        )

    if dry_run:
        console.print(ops_viewer.dry_run_message())
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
    operation_plan_results = run_plan(operation_plan, console, show_hints, True)
    ops_viewer.check_operation_succeeded(operation_plan_results)

    console.print(f"Disable maintenance for node: {node}")
