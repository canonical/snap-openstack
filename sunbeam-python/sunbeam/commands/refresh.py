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

import click
from rich.console import Console
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.commands.terraform import TerraformHelper
from sunbeam.commands.upgrades.inter_channel import ChannelUpgradeCoordinator
from sunbeam.commands.upgrades.intra_channel import LatestInChannelCoordinator
from sunbeam.jobs.juju import JujuHelper

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click.option(
    "--upgrade-release",
    is_flag=True,
    show_default=True,
    default=False,
    help="Upgrade OpenStack release.",
)
@click.pass_context
def refresh(ctx: click.Context, upgrade_release: bool) -> None:
    """Refresh deployment.

    Refresh the deployment. If --upgrade-release is supplied then charms are
    upgraded the channels aligned with this snap revision
    """
    tfplan = "deploy-openstack"
    data_location = snap.paths.user_data
    client: Client = ctx.obj
    tfhelper = TerraformHelper(
        path=snap.paths.user_common / "etc" / tfplan,
        plan="openstack-plan",
        backend="http",
        data_location=data_location,
    )
    jhelper = JujuHelper(client, data_location)
    if upgrade_release:
        a = ChannelUpgradeCoordinator(client, jhelper, tfhelper)
        a.run_plan()
    else:
        a = LatestInChannelCoordinator(jhelper, tfhelper)
        a.run_plan()
    click.echo("Refresh complete.")
