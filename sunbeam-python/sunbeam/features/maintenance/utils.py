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
    MicroCephActionStep,
    RunWatcherAuditStep,
)

console = Console()
LOG = logging.getLogger(__name__)


def get_node_status(
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
        return (
            f"Required operations to put {self.node} into "
            f"maintenance mode:{linesep}{self._operation_plan}"
        )

    @staticmethod
    def _get_watcher_action_key(action: watcher.Action) -> str:
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

    def add_watch_actions(self, actions: list[watcher.Action]):
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
        question: Question = ConfirmQuestion(
            f"Continue to run operations to put {self.node} into"
            f" maintenance mode:{linesep}{self._operation_plan}"
        )
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
        console.print(self._operation_result)
        if failed_result is not None and failed_result_name is not None:
            self._raise_exception(failed_result_name, failed_result)

    def _raise_exception(self, name: str, result: Result):
        if name == MicroCephActionStep.__name__:
            raise click.ClickException(result.message.get("errors"))
        raise click.ClickException(result.message)
