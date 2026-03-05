# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from packaging.version import Version

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
        """Manage maintenance mode."""

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
