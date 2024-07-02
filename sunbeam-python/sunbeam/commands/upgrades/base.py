# Copyright (c) 2023 Canonical Ltd.
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
from typing import Optional

from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.jobs.common import BaseStep, Result, ResultType, run_plan
from sunbeam.jobs.deployments import Deployment
from sunbeam.jobs.juju import JujuHelper
from sunbeam.jobs.manifest import Manifest
from sunbeam.jobs.plugin import PluginManager

LOG = logging.getLogger(__name__)
console = Console()


class UpgradePlugins(BaseStep):
    def __init__(
        self,
        deployment: Deployment,
        upgrade_release: bool = False,
    ):
        """Upgrade plugins.

        :client: Helper for interacting with clusterd
        :upgrade_release: Whether to upgrade channel
        """
        super().__init__("Validation", "Running pre-upgrade validation")
        self.deployment = deployment
        self.upgrade_release = upgrade_release

    def run(self, status: Optional[Status] = None) -> Result:
        """Upgrade plugins."""
        PluginManager.update_plugins(
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
        self.k8s_provider = Snap().config.get("k8s.provider")

    def get_plan(self) -> list[BaseStep]:
        """Return the plan for this upgrade.

        Return the steps to complete this upgrade.
        """
        return []

    def run_plan(self) -> None:
        """Execute the upgrade plan."""
        plan = self.get_plan()
        run_plan(plan, console)
