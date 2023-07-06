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

from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.jobs import juju
from sunbeam.jobs.checks import DaemonGroupCheck
from sunbeam.jobs.common import run_preflight_checks

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
def openrc() -> None:
    """Retrieve openrc for cloud admin account."""
    preflight_checks = []
    preflight_checks.append(DaemonGroupCheck())
    run_preflight_checks(preflight_checks, console)

    data_location = snap.paths.user_data
    jhelper = juju.JujuHelper(data_location)

    with console.status("Retrieving openrc from Keystone service ... "):
        # Retrieve config from juju actions
        model = OPENSTACK_MODEL
        app = "keystone"
        action_cmd = "get-admin-account"
        unit = juju.run_sync(jhelper.get_leader_unit(app, model))
        if not unit:
            _message = f"Unable to get {app} leader"
            raise click.ClickException(_message)

        action_result = juju.run_sync(jhelper.run_action(unit, model, action_cmd))

        if action_result.get("return-code", 0) > 1:
            _message = "Unable to retrieve openrc from Keystone service"
            raise click.ClickException(_message)
        else:
            console.print(action_result.get("openrc"))
