# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

# Multi-region related utilities.

import base64
import json
import logging

from rich.console import Console
from snaphelpers import Snap

from sunbeam.core.common import (
    BaseStep,
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuAccount,
    JujuAccountNotFound,
    JujuController,
    JujuHelper,
)
from sunbeam.steps.juju import (
    CheckJujuReachableStep,
    JujuLoginStep,
    RegisterRemoteJujuUserStep,
    SwitchToController,
)

LOG = logging.getLogger(__name__)
console = Console()


def connect_to_region_controller(
    deployment: Deployment,
    region_controller_token: str,
    initial_controller: str,
    show_hints: bool = False,
):
    """Connect to the region controller using the specified token.

    Returns a tuple containing the Juju controller name and the
    primary region name.
    """
    LOG.debug("Connecting to the region controller.")
    snap = Snap()
    data_location = snap.paths.user_data

    region_controller_info = json.loads(
        base64.b64decode(region_controller_token).decode()
    )
    primary_region_name = region_controller_info["primary_region_name"]
    region_controller_juju_ctrl = JujuController(
        **region_controller_info["juju_controller"]
    )
    # We'll probably get the default "sunbeam-controller" name,
    # let's add the "-region-controller" suffix to avoid duplicates.
    region_controller_juju_ctrl.name += "-region-controller"
    region_ctrl_name = region_controller_juju_ctrl.name

    if (
        deployment.primary_region_name
        and deployment.primary_region_name != primary_region_name
    ):
        raise ValueError(
            "The primary region name associated with this deployment "
            f"({deployment.primary_region_name}) does not match the region "
            f"of the token ({primary_region_name})"
        )
    if deployment.get_region_name() == primary_region_name:
        raise ValueError(
            "The secondary region can not have the same name "
            f"as the primary region: {deployment.get_region_name()}"
        )

    logging.debug(
        "Primary region name: %s, secondary region name: %s",
        primary_region_name,
        deployment.get_region_name(),
    )

    juju_registration_token = region_controller_info["juju_registration_token"]
    try:
        region_ctrl_account = JujuAccount.load(
            data_location, f"{region_ctrl_name}.yaml"
        )
        already_registered = True
    except JujuAccountNotFound:
        region_ctrl_account = None
        already_registered = False

    region_plan: list[BaseStep] = []
    if already_registered:
        region_plan += [
            JujuLoginStep(region_ctrl_account, region_ctrl_name),
        ]
    else:
        region_plan += [
            CheckJujuReachableStep(region_controller_juju_ctrl),
            RegisterRemoteJujuUserStep(
                juju_registration_token, region_ctrl_name, data_location
            ),
            SwitchToController(initial_controller),
        ]
    run_plan(region_plan, console, show_hints)

    region_jhelper = JujuHelper(region_controller_juju_ctrl)
    openstack_model_with_owner = region_jhelper.get_model_name_with_owner("openstack")

    deployment.external_keystone_model = (
        f"{region_ctrl_name}:{openstack_model_with_owner}"
    )
    if not deployment.primary_region_name:
        deployment.primary_region_name = primary_region_name
    if not deployment.region_ctrl_juju_account:
        deployment.region_ctrl_juju_account = region_ctrl_account
    if not deployment.region_ctrl_juju_controller:
        deployment.region_ctrl_juju_controller = region_controller_juju_ctrl
