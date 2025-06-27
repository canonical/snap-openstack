# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest

LOG = logging.getLogger(__name__)
console = Console()


class UpgradeFeatures(BaseStep):
    def __init__(
        self,
        deployment: Deployment,
        upgrade_release: bool = False,
    ):
        """Upgrade features.

        :client: Helper for interacting with clusterd
        :upgrade_release: Whether to upgrade channel
        """
        super().__init__("Validation", "Running pre-upgrade validation")
        self.deployment = deployment
        self.upgrade_release = upgrade_release

    def run(self, status: Status | None = None) -> Result:
        """Upgrade features."""
        self.deployment.get_feature_manager().update_features(
            self.deployment, upgrade_release=self.upgrade_release
        )
        return Result(ResultType.COMPLETED)


class UpgradeCoordinator:
    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        manifest: Manifest,
    ):
        """Upgrade coordinator.

        Execute plan for conducting an upgrade.

        :client: Helper for interacting with clusterd
        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        """
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.manifest = manifest

    def get_plan(self) -> list[BaseStep]:
        """Return the plan for this upgrade.

        Return the steps to complete this upgrade.
        """
        return []

    def run_plan(self, show_hints: bool = False) -> None:
        """Execute the upgrade plan."""
        plan = self.get_plan()
        run_plan(plan, console, show_hints)
