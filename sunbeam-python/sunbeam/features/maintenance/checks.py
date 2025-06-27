# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any

from rich.console import Console

from sunbeam.clusterd.client import Client
from sunbeam.core.checks import Check
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuActionHelper,
    JujuHelper,
    UnitNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.openstack_api import (
    get_admin_connection,
    guests_on_hypervisor,
)
from sunbeam.core.watcher import WATCHER_APPLICATION
from sunbeam.steps.microceph import APPLICATION as _MICROCEPH_APPLICATION

console = Console()
LOG = logging.getLogger(__name__)


class InstancesStatusCheck(Check):
    """Detect if any instance in unexpected status.

    - If there are any instance in ERROR status,
        operator should manually handle it first.
    - If there are any instance in MIGRATING status,
        operator should wait until migration finished.
    - If there are any instance in SHUTOFF status,
        the maintenance will be blocked, because sunbeam doesn't support
        cold migration now. this will blocked until we have disable cold migration
        feature support in watcher. see:
        https://bugs.launchpad.net/snap-openstack/+bug/2082056 and
        https://review.opendev.org/c/openstack/watcher-specs/+/943873
    """

    def __init__(self, jhelper: JujuHelper, node: str, force: bool):
        super().__init__(
            "Check no instance in ERROR/MIGRATING/SHUTOFF status on nodes",
            (
                "Checking if there are any instance in"
                " ERROR/MIGRATING/SHUTOFF status on nodes"
            ),
        )
        self.jhelper = jhelper
        self.node = node
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        conn = get_admin_connection(jhelper=self.jhelper)

        not_expected_status_instances: dict[str, str] = {}

        for status in ["ERROR", "MIGRATING", "SHUTOFF"]:
            for inst in guests_on_hypervisor(
                hypervisor_name=self.node,
                conn=conn,
                status=status,
            ):
                not_expected_status_instances[inst.id] = status

        if not_expected_status_instances:
            _msg = f"Instances not in expected status: {not_expected_status_instances}"
            if self.force:
                LOG.warning(f"Ignore issue: {_msg}")
                return True
            self.message = _msg
            return False
        return True


class NoEphemeralDiskCheck(Check):
    def __init__(self, jhelper: JujuHelper, node: str, force: bool):
        super().__init__(
            "Check no instance using ephemeral disk",
            "Checking if there are any instance is using ephemeral disk",
        )
        self.jhelper = jhelper
        self.node = node
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        conn = get_admin_connection(jhelper=self.jhelper)

        unexpected_instances = []

        for inst in guests_on_hypervisor(
            hypervisor_name=self.node,
            conn=conn,
        ):
            flavor = conn.compute.find_flavor(inst.flavor.get("id"))
            if flavor.ephemeral > 0:
                unexpected_instances.append(inst.id)
        if unexpected_instances:
            _msg = f"Instances have ephemeral disk: {unexpected_instances}"
            if self.force:
                LOG.warning(f"Ignore issue: {_msg}")
                return True
            self.message = _msg
            return False
        return True


class NoInstancesOnNodeCheck(Check):
    def __init__(self, jhelper: JujuHelper, node: str, force: bool):
        super().__init__(
            "Check no instance on the node",
            "Check no instance on the node",
        )
        self.jhelper = jhelper
        self.node = node
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        conn = get_admin_connection(jhelper=self.jhelper)

        instances = guests_on_hypervisor(hypervisor_name=self.node, conn=conn)

        if len(instances) > 0:
            instance_ids = ",".join([inst.id for inst in instances])
            _msg = f"Instances {instance_ids} still on node {self.node}"
            if self.force:
                LOG.warning(f"Ignore issue: {_msg}")
                return True
            self.message = _msg
            return False
        return True


class NovaInDisableStatusCheck(Check):
    def __init__(self, jhelper: JujuHelper, node: str, force: bool):
        super().__init__(
            "Check nova compute is disable on the node",
            "Check nova compute is disable on the node",
        )
        self.jhelper = jhelper
        self.node = node
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        conn = get_admin_connection(jhelper=self.jhelper)

        expected_services = []
        for svc in conn.compute.services(
            binary="nova-compute", host=self.node, status="disabled"
        ):
            expected_services.append(svc.id)

        if not len(expected_services) == 1:
            _msg = f"Nova compute still not disabled on node {self.node}"
            if self.force:
                LOG.warning(f"Ignore issue: {_msg}")
                return True
            self.message = _msg
            return False
        return True


class MicroCephMaintenancePreflightCheck(Check):
    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        node: str,
        action_params: dict[str, Any],
        force: bool,
    ):
        super().__init__(
            "Run MicroCeph enter maintenance preflight checks",
            "Run MicroCeph enter maintenance preflight checks",
        )
        self.client = client
        self.node = node
        self.jhelper = jhelper
        self.model = model
        self.action_params = action_params
        self.action_params["dry-run"] = False
        self.action_params["check-only"] = True
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        try:
            JujuActionHelper.run_action(
                client=self.client,
                jhelper=self.jhelper,
                model=self.model,
                node=self.node,
                app=_MICROCEPH_APPLICATION,
                action_name="enter-maintenance",
                action_params=self.action_params,
            )
        except UnitNotFoundException:
            self.message = (
                f"App {_MICROCEPH_APPLICATION} unit not found on node {self.node}"
            )
            return False
        except ActionFailedException as e:
            for _, action in e.action_result.get("actions", {}).items():
                if action.get("error"):
                    msg = action.get("error")
                    if self.force:
                        LOG.warning(f"Ignore issue: {msg}")
                    else:
                        self.message = msg
                        return False
        return True


class WatcherApplicationExistsCheck(Check):
    """Make sure watcher application exists in model."""

    def __init__(
        self,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Check if watcher is deployed.",
            "Check if watcher is deployed.",
        )
        self.jhelper = jhelper

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        try:
            self.jhelper.get_application(
                name=WATCHER_APPLICATION,
                model=OPENSTACK_MODEL,
            )
        except ApplicationNotFoundException:
            self.message = (
                "Watcher not found, please deploy watcher with command:"
                " `sunbeam enable resource-optimization` before continue"
            )
            return False
        return True
