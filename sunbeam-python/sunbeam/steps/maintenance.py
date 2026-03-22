# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import tenacity
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.core import watcher as watcher_helper
from sunbeam.core.common import BaseStep, Result, ResultType, SunbeamException
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    JujuActionHelper,
    JujuHelper,
    UnitNotFoundException,
)
from sunbeam.core.watcher import WatcherActionFailedException
from sunbeam.steps.k8s import (
    CordonK8SUnitStep,
    DrainK8SUnitStep,
    UncordonK8SUnitStep,
)
from sunbeam.steps.microceph import APPLICATION as _MICROCEPH_APPLICATION

if TYPE_CHECKING:
    from watcherclient import v1 as watcher
    from watcherclient.v1 import client as watcher_client

LOG = logging.getLogger(__name__)


class MicroCephActionStep(BaseStep):
    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        node: str,
        model: str,
        action_name: str,
        action_params: dict[str, Any],
    ):
        name = f"Run action {action_name} on microceph node {node}"
        description = f"Run action {action_name} on microceph node {node}"
        super().__init__(name, description)

        self.client = client
        self.node = node
        self.jhelper = jhelper
        self.model = model
        self.action_name = action_name
        self.action_params = action_params
        self.app = _MICROCEPH_APPLICATION

    def run(self, status: Status | None = None) -> Result:
        """Run charm microceph action."""
        failed: bool = False
        message: str = ""
        try:
            action_result = JujuActionHelper.run_action(
                client=self.client,
                jhelper=self.jhelper,
                model=self.model,
                node=self.node,
                app=self.app,
                action_name=self.action_name,
                action_params=self.action_params,
            )
        except UnitNotFoundException as e:
            message = f"Microceph node {self.node} not found: {str(e)}"
            failed = True
        except ActionFailedException as e:
            message = e.action_result
            failed = True
        if failed:
            return Result(ResultType.FAILED, message)
        return Result(ResultType.COMPLETED, action_result)


class CreateWatcherAuditStepABC(ABC, BaseStep):
    def __init__(
        self,
        deployment: Deployment,
        node: str,
    ):
        super().__init__(self.name, self.description)
        self.node = node
        self.client: "watcher_client.Client" = watcher_helper.get_watcher_client(
            deployment=deployment
        )

    @abstractmethod
    def _create_audit(self) -> "watcher.Audit":
        """Create Watcher audit."""
        raise NotImplementedError

    def _get_actions(self, audit: "watcher.Audit") -> list["watcher.Action"]:
        return watcher_helper.get_actions(client=self.client, audit=audit)

    def run(self, status: Status | None) -> Result:
        """Create Watcher audit."""
        try:
            audit = self._create_audit()
            actions = self._get_actions(audit)
        except tenacity.RetryError as e:
            LOG.warning(e)
            return Result(ResultType.FAILED, "Unable to create Watcher audit")
        return Result(
            ResultType.COMPLETED,
            {
                "audit": audit,
                "actions": actions,
            },
        )


class CreateWatcherHostMaintenanceAuditStep(CreateWatcherAuditStepABC):
    name = "Create Watcher Host maintenance audit"
    description = "Create Watcher Host maintenance audit"

    def __init__(
        self,
        deployment: Deployment,
        node: str,
        disable_live_migration: bool = False,
        disable_cold_migration: bool = False,
    ):
        super().__init__(deployment=deployment, node=node)
        self.disable_live_migration = disable_live_migration
        self.disable_cold_migration = disable_cold_migration

    def _create_audit(self) -> "watcher.Audit":
        audit_template = watcher_helper.get_enable_maintenance_audit_template(
            client=self.client
        )

        parameters = {
            "maintenance_node": self.node,
            "disable_live_migration": self.disable_live_migration,
            "disable_cold_migration": self.disable_cold_migration,
        }

        return watcher_helper.create_audit(
            client=self.client,
            template=audit_template,
            parameters=parameters,
        )


class CreateWatcherWorkloadBalancingAuditStep(CreateWatcherAuditStepABC):
    name = "Create Watcher workload balancing audit"
    description = "Create Watcher workload balancing audit"

    def _create_audit(self) -> "watcher.Audit":
        audit_template = watcher_helper.get_workload_balancing_audit_template(
            client=self.client
        )
        return watcher_helper.create_audit(
            client=self.client,
            template=audit_template,
        )


class RunWatcherAuditStep(BaseStep):
    name = "Start Watcher Audit's action plan"
    description = "Start Watcher Audit's action plan"

    def __init__(
        self,
        deployment: Deployment,
        node: str,
        audit: "watcher.Audit",
    ):
        self.node = node
        super().__init__(self.name, self.description)
        self.client: "watcher_client.Client" = watcher_helper.get_watcher_client(
            deployment=deployment
        )
        self.audit = audit

    def run(self, status: Status | None) -> Result:
        """Execute Watcher Audit's Action Plan."""
        failed = False
        try:
            watcher_helper.exec_audit(self.client, self.audit)
            watcher_helper.wait_until_action_state(
                step=self,
                audit=self.audit,
                client=self.client,
                status=status,
            )
        except (
            SunbeamException,
            tenacity.RetryError,
            WatcherActionFailedException,
        ) as e:
            LOG.warning(e)
            failed = True

        actions = watcher_helper.get_actions(client=self.client, audit=self.audit)
        return Result(
            ResultType.COMPLETED if not failed else ResultType.FAILED,
            actions,
        )


class DrainControlRoleNodeStep(DrainK8SUnitStep):
    def __init__(
        self,
        node: str,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        dry_run: bool = True,
    ):
        super().__init__(client, node, jhelper, model)
        self.dry_run = dry_run

    def run(self, status: Status | None = None) -> Result:
        """Execute drain control role node step."""
        step_key = f"Drain '{self.node}'"

        if self.dry_run:
            return Result(ResultType.COMPLETED, {"id": step_key})

        result = super().run(status)
        result.message = {"id": step_key}
        return result


class CordonControlRoleNodeStep(CordonK8SUnitStep):
    def __init__(
        self,
        node: str,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        dry_run: bool = True,
    ):
        super().__init__(client, node, jhelper, model)
        self.dry_run = dry_run

    def run(self, status: Status | None = None) -> Result:
        """Execute cordon control role node step."""
        step_key = f"Cordon '{self.node}'"

        if self.dry_run:
            return Result(ResultType.COMPLETED, {"id": step_key})

        result = super().run(status)
        result.message = {"id": step_key}
        return result


class UncordonControlRoleNodeStep(UncordonK8SUnitStep):
    def __init__(
        self,
        node: str,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        dry_run: bool = True,
    ):
        super().__init__(client, node, jhelper, model)
        self.dry_run = dry_run

    def run(self, status: Status | None = None) -> Result:
        """Execute uncordon control role node step."""
        step_key = f"Uncordon '{self.node}'"

        if self.dry_run:
            return Result(ResultType.COMPLETED, {"id": step_key})

        result = super().run(status)
        result.message = {"id": step_key}
        return result
