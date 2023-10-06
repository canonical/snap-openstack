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

"""Plugin to add any migration tasks."""

import logging
import shutil
from typing import Optional

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.commands.microk8s import DeployMicrok8sAddonsStep
from sunbeam.commands.terraform import TerraformHelper, TerraformInitStep
from sunbeam.jobs.common import BaseStep, Result, ResultType, run_plan
from sunbeam.jobs.juju import JujuHelper
from sunbeam.plugins.interface.v1.base import BasePlugin
from sunbeam.utils import CatchGroup

LOG = logging.getLogger(__name__)
console = Console()


class RemoveMicrok8sAddonsTerraformVarStep(BaseStep):
    def __init__(self):
        super().__init__(
            "Remove Unnecessary files",
            "Remove addons terraform vars",
        )

    def run(self, status: Optional[Status] = None) -> Result:
        """Remove addons.auto.tfvars.json"""
        snap = Snap()
        addons_file = (
            snap.paths.user_common
            / "etc"  # noqa: W503
            / "deploy-microk8s"  # noqa: W503
            / "addons.auto.tfvars.json"  # noqa: W503
        )
        addons_file.unlink(missing_ok=True)

        return Result(ResultType.COMPLETED)


class MigrateMicrok8sStep(DeployMicrok8sAddonsStep):
    """Migrate Microk8s from legacy"""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
    ):
        super().__init__(tfhelper, jhelper)

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if self.client.cluster.list_nodes_by_role("control"):
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False


class MigratePlugin(BasePlugin):
    version = Version("0.0.1")

    def __init__(self) -> None:
        self.name = "migrate"
        super().__init__(name=self.name)

    def commands(self) -> dict:
        return {
            "init": [{"name": self.name, "command": self.migrate}],
            "migrate": [
                {"name": "microk8s", "command": self.microk8s},
            ],
        }

    @click.group("migrate", cls=CatchGroup)
    def migrate(self):
        """Manage migrations."""

    @click.command()
    def microk8s(self) -> None:
        """Migrate charm microk8s from legacy."""
        snap = Snap()

        for tfplan_dir in ["deploy-microk8s"]:
            src = snap.paths.snap / "etc" / tfplan_dir
            dst = snap.paths.user_common / "etc" / tfplan_dir
            LOG.debug(f"Updating {dst} from {src}...")
            shutil.copytree(src, dst, dirs_exist_ok=True)

        data_location = snap.paths.user_data
        jhelper = JujuHelper(data_location)
        tfhelper = TerraformHelper(
            path=snap.paths.user_common / "etc" / "deploy-microk8s",
            plan="microk8s-plan",
            backend="http",
            data_location=data_location,
        )

        plan = []
        # TODO(hemanth): Add a step to determine if deployed charm microk8s
        # channel is legacy/stable or not
        plan.append(RemoveMicrok8sAddonsTerraformVarStep())
        plan.append(TerraformInitStep(tfhelper))
        # TOCHK(hemanth): Verify once the below bugs are resolved
        # https://github.com/juju/terraform-provider-juju/issues/249
        # https://github.com/juju/terraform-provider-juju/issues/278
        plan.append(MigrateMicrok8sStep(tfhelper, jhelper))

        run_plan(plan, console)
        click.echo("Migration from legacy microk8s charm completed.")
