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

from sunbeam.core import juju
from sunbeam.core.checks import VerifyBootstrappedCheck, run_preflight_checks
from sunbeam.core.deployment import Deployment
from sunbeam.core.openstack import OPENSTACK_MODEL

LOG = logging.getLogger(__name__)
console = Console()


def retrieve_dashboard_url(jhelper: juju.JujuHelper) -> str:
    """Retrieve dashboard URL from Horizon service."""
    model = OPENSTACK_MODEL
    app = "horizon"
    action_cmd = "get-dashboard-url"
    try:
        unit = juju.run_sync(jhelper.get_leader_unit(app, model))
    except juju.LeaderNotFoundException:
        raise ValueError(f"Unable to get {app} leader")
    action_result = juju.run_sync(jhelper.run_action(unit, model, action_cmd))
    if action_result.get("return-code", 0) > 1:
        _message = "Unable to retrieve URL from Horizon service"
        raise ValueError(_message)
    else:
        return action_result["url"]


@click.command()
@click.pass_context
def dashboard_url(ctx: click.Context) -> None:
    """Retrieve OpenStack Dashboard URL."""
    deployment: Deployment = ctx.obj
    preflight_checks = []
    preflight_checks.append(VerifyBootstrappedCheck(deployment.get_client()))
    run_preflight_checks(preflight_checks, console)
    jhelper = juju.JujuHelper(deployment.get_connected_controller())

    with console.status("Retrieving dashboard URL from Horizon service ... "):
        try:
            console.print(retrieve_dashboard_url(jhelper))
        except Exception as e:
            raise click.ClickException(str(e))
