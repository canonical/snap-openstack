# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import enum
import logging
from os import linesep
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console

from sunbeam.core.common import (
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
from sunbeam.steps.cluster_status import ClusterStatusStep
from sunbeam.steps.hypervisor import EnableHypervisorStep
from sunbeam.steps.maintenance import (
    CordonControlRoleNodeStep,
    DrainControlRoleNodeStep,
    MicroCephActionStep,
    RunWatcherAuditStep,
    UncordonControlRoleNodeStep,
)

if TYPE_CHECKING:
    from watcherclient import v1 as watcher


console = Console()
LOG = logging.getLogger(__name__)


def get_cluster_status(
    deployment: Deployment,
    jhelper: JujuHelper,
    console: Console,
    show_hints: bool,
) -> dict[str, Any]:
    cluster_status_step: type[ClusterStatusStep]
    if deployment.type == "local":
        cluster_status_step = LocalClusterStatusStep
    else:
        cluster_status_step = MaasClusterStatusStep

    results = run_plan([cluster_status_step(deployment, jhelper)], console, show_hints)
    cluster_status = get_step_message(results, cluster_status_step)

    return {
        machine["hostname"]: machine["status"]
        for machine in cluster_status[deployment.openstack_machines_model].values()
    }


class OperationGoal(enum.Enum):
    EnableMaintenance = "EnableMaintenance"
    DisableMaintenance = "DisableMaintenance"


class OperationViewer:
    def __init__(
        self, node: str, goal: OperationGoal = OperationGoal.EnableMaintenance
    ):
        self.node = node
        self.operations: list[str] = []
        self.operation_states: dict[str, str] = {}
        self.goal = goal

    @property
    def _operation_plan(self) -> str:
        """Return planned opertions str."""
        msg = ""
        for idx, step in enumerate(self.operations):
            msg += f"\t{idx}: {step}{linesep}"
        return msg

    @property
    def _operation_result(self) -> str:
        """Return result of operations as str."""
        msg = ""
        for idx, step in enumerate(self.operations):
            msg += f"\t{idx}: {step} {self.operation_states[step]}{linesep}"
        if msg:
            msg = f"Operation result:{linesep}" + msg
        return msg

    @property
    def dry_run_message(self) -> str:
        """Return CLI output message for dry-run."""
        if self.goal == OperationGoal.DisableMaintenance:
            return (
                "Required operations to disable maintenance mode"
                f" for {self.node}:{linesep}{self._operation_plan}"
            )
        # EnableMaintenance
        return (
            "Required operations to enable maintenance mode"
            f" for {self.node}:{linesep}{self._operation_plan}"
        )

    @staticmethod
    def _get_watcher_action_key(action: "watcher.Action") -> str:
        """Return rich information key base on different type of action."""
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

    def add_watch_actions(self, actions: list["watcher.Action"]):
        """Append Watcher actions to operations."""
        for action in actions:
            key = self._get_watcher_action_key(action)
            self.operations.append(key)
            self.operation_states[key] = "PENDING"

    def add_maintenance_action_steps(self, action_result: dict[str, Any]):
        """Append juju maintenance action's actions to operations.

        This handle the juju action output like charm-microceph
        enter-maintenance or exit-maintenance.
        The output format can be found on:
        https://github.com/canonical/charm-microceph/blob/main/src/maintenance.py
        """
        for step, action in action_result.get("actions", {}).items():
            self.operations.append(action["id"])
            self.operation_states[action["id"]] = "SKIPPED"

    def add_drain_control_role_step(self, result: dict[str, Any]):
        """Append drain control role node step to operations."""
        self.operations.append(result["id"])
        self.operation_states[result["id"]] = "SKIPPED"

    def add_cordon_control_role_step(self, result: dict[str, Any]):
        """Append cordon control role node step to operations."""
        self.operations.append(result["id"])
        self.operation_states[result["id"]] = "SKIPPED"

    def add_uncordon_control_role_step(self, result: dict[str, Any]):
        """Append uncordon control role node step to operations."""
        self.operations.append(result["id"])
        self.operation_states[result["id"]] = "SKIPPED"

    def add_step(self, step_name: str):
        """Append BaseStep to operations."""
        self.operations.append(step_name)
        self.operation_states[step_name] = "SKIPPED"

    def update_watcher_actions_result(self, actions: list["watcher.Action"]):
        """Update result of Watcher actions."""
        for action in actions:
            key = self._get_watcher_action_key(action)
            self.operation_states[key] = action.state

    def update_maintenance_action_steps_result(self, action_result: dict[str, Any]):
        """Update result of juju maintenance action's actions.

        This handle the juju action output like charm-microceph
        enter-maintenance or exit-maintenance.
        The output format can be found on:
        https://github.com/canonical/charm-microceph/blob/main/src/maintenance.py
        """
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

    def prompt(self) -> bool:
        """Determines if the operations is confirmed by the user."""
        # EnableMaintenance
        prefix = (
            f"Continue to run operation to enable maintenance mode for {self.node}:"
        )
        if self.goal == OperationGoal.DisableMaintenance:
            prefix = (
                f"Continue to run operation to disable maintenance"
                f" mode for {self.node}:"
            )
        question: Question = ConfirmQuestion(prefix + linesep + self._operation_plan)
        return question.ask() or False

    def check_operation_succeeded(self, results: dict[str, Result]):
        """Check if all the operations are succeeded."""
        failed_result_name: str | None = None
        failed_result: Result | None = None
        for name, result in results.items():
            if result.result_type == ResultType.FAILED:
                failed_result = result
                failed_result_name = name
            if name == RunWatcherAuditStep.__name__:
                self.update_watcher_actions_result(result.message)
            elif name == MicroCephActionStep.__name__:
                self.update_maintenance_action_steps_result(result.message)
            elif name == EnableHypervisorStep.__name__:
                self.update_step_result(name, result)
            elif name == DrainControlRoleNodeStep.__name__:
                self.update_step_result(result.message["id"], result)
            elif name == CordonControlRoleNodeStep.__name__:
                self.update_step_result(result.message["id"], result)
            elif name == UncordonControlRoleNodeStep.__name__:
                self.update_step_result(result.message["id"], result)
        console.print(self._operation_result)
        if failed_result is not None and failed_result_name is not None:
            self._raise_exception(failed_result_name, failed_result)

    def _raise_exception(self, name: str, result: Result):
        if name == MicroCephActionStep.__name__:
            raise click.ClickException(result.message.get("errors"))
        raise click.ClickException(result.message)
