# Copyright (c) 2025 Canonical Ltd.
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

import click
from packaging.version import Version

from sunbeam.core.common import RiskLevel
from sunbeam.core.deployment import Deployment
from sunbeam.core.manifest import FeatureConfig
from sunbeam.features.interface.v1.base import (
    ConfigType,
    EnableDisableFeature,
    FeatureRequirement,
)
from sunbeam.features.maintenance.commands import (
    disable as disable_maintenance_cmd,
)
from sunbeam.features.maintenance.commands import (
    enable as enable_maintenance_cmd,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj

LOG = logging.getLogger(__name__)


class MaintenanceFeature(EnableDisableFeature):
    version = Version("0.0.1")

    # Compute role maintenance depends on watcher
    requires = {FeatureRequirement("resource-optimization")}

    name = "maintenance"
    risk_availability = RiskLevel.EDGE

    def run_enable_plans(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Run plans to enable feature.

        This feature only register commands, so skip.
        """
        pass

    def run_disable_plans(self, deployment: Deployment, show_hints: bool):
        """Run plans to disable the feature.

        This feature only register commands, so skip.
        """
        pass

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable maintenance support."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable maintenance support."""
        self.disable_feature(deployment, show_hints)

    @click.group()
    def maintenance_group(self) -> None:
        """Group command."""

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            "cluster": [{"name": "maintenance", "command": self.maintenance_group}],
            "cluster.maintenance": [
                {"name": "enable", "command": enable_maintenance_cmd},
                {"name": "disable", "command": disable_maintenance_cmd},
            ],
        }
