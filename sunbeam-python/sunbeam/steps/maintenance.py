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
from abc import ABC, abstractmethod

from rich.console import Console
from rich.status import Status
from watcherclient import v1 as watcher
from watcherclient.v1 import client as watcher_client

from sunbeam.core import watcher as watcher_helper
from sunbeam.core.common import BaseStep, Result, ResultType, SunbeamException
from sunbeam.core.deployment import Deployment
from sunbeam.core.questions import ConfirmQuestion

LOG = logging.getLogger(__name__)


class WatcherStepMixin(ABC, BaseStep):
    name: str = ""
    description: str = ""

    def __init__(
        self,
        deployment: Deployment,
    ):
        super().__init__(self.name, self.description)
        self.client: watcher_client.Client = watcher_helper.get_watcher_client(
            deployment=deployment
        )

        self.confirm: bool | None = False
        self.audit: watcher.Audit

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.confirm:
            return Result(ResultType.COMPLETED)
        return Result(ResultType.SKIPPED)

    def _confirm_audit(self, audit) -> bool | None:
        """Get actions created by audit and get confirm from user."""
        actions = watcher_helper.get_actions(client=self.client, audit=audit)
        action_operation_msg = ""
        if len(actions) == 0:
            return True
        for idx, action in enumerate(actions):
            action_operation_msg += (
                f"\t{idx}. {action.description}"
                f" | parameters: {action.input_parameters}\n"
            )
        question = ConfirmQuestion(
            ("Confirm to run operations for cluster:\n" f"{action_operation_msg}"),
            default_value=False,
            description=(
                "This will confirm to execute the operation to enable maintenance mode"
            ),
        )
        return question.ask()

    @abstractmethod
    def _create_audit(self) -> watcher.Audit:
        """Create Watcher audit."""
        raise NotImplementedError

    def prompt(self, console: Console | None = None, show_hint: bool = False) -> None:
        """Determines if the step can take input from the user."""
        self.audit = self._create_audit()
        self.confirm = self._confirm_audit(audit=self.audit)

    def run(self, status: Status | None) -> Result:
        """Execute Watcher audit's actions."""
        try:
            watcher_helper.exec_audit(self.client, self.audit)
        except SunbeamException as e:
            return Result(ResultType.FAILED, str(e))

        return Result(
            ResultType.COMPLETED,
            "",
        )


class RunWatcherHostMaintenanceStep(WatcherStepMixin):
    name = "Run host maintenance audit with Watcher"
    description = "Run host maintenance audit with Watcher"

    def __init__(
        self,
        deployment: Deployment,
        node: str,
    ):
        self.node = node
        super().__init__(deployment=deployment)

    def _create_audit(self) -> watcher.Audit:
        audit_template = watcher_helper.get_enable_maintenance_audit_template(
            client=self.client
        )
        return watcher_helper.create_audit(
            client=self.client,
            template=audit_template,
            parameters={"maintenance_node": self.node},
        )


class RunWatcherWorkloadBalancingStep(WatcherStepMixin):
    name = "Run workload balancing audit with Watcher"
    description = "Run workload balancing audit with Watcher"

    def _create_audit(self) -> watcher.Audit:
        audit_template = watcher_helper.get_workload_balancing_audit_template(
            client=self.client
        )
        return watcher_helper.create_audit(
            client=self.client,
            template=audit_template,
        )
