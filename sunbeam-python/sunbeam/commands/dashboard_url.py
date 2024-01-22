# Copyright (c) 2022 Canonical Ltd.
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
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.jobs import juju
from sunbeam.jobs.checks import VerifyBootstrappedCheck
from sunbeam.jobs.common import run_preflight_checks

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click.pass_context
def dashboard_url(ctx: click.Context) -> None:
    """Retrieve OpenStack Dashboard URL."""
    client: Client = ctx.obj
    preflight_checks = []
    preflight_checks.append(VerifyBootstrappedCheck(client))
    run_preflight_checks(preflight_checks, console)
    data_location = snap.paths.user_data
    jhelper = juju.JujuHelper(client, data_location)

    with console.status("Retrieving dashboard URL from Horizon service ... "):
        # Retrieve config from juju actions
        model = OPENSTACK_MODEL
        app = "horizon"
        action_cmd = "get-dashboard-url"
        unit = juju.run_sync(jhelper.get_leader_unit(app, model))
        if not unit:
            _message = f"Unable to get {app} leader"
            raise click.ClickException(_message)

        action_result = juju.run_sync(jhelper.run_action(unit, model, action_cmd))

        if action_result.get("return-code", 0) > 1:
            _message = "Unable to retrieve URL from Horizon service"
            raise click.ClickException(_message)
        else:
            console.print(action_result.get("url"))
