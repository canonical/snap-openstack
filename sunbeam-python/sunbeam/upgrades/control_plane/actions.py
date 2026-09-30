# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Pre/post-upgrade action orchestration for control-plane groups.

Runs juju actions declared in the group's ``pre_actions`` and
``post_actions`` metadata. Pre-actions run before terraform apply;
post-actions run after convergence (or after failure, as cleanup).
"""

from __future__ import annotations

import logging
import time

from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.upgrades.coordinator import PhaseResult
from sunbeam.upgrades.errors import UpgradeErrorCode
from sunbeam.upgrades.metadata import ActionScope, ActionSpec

LOG = logging.getLogger(__name__)

# Seconds to wait after pre-upgrade actions before proceeding with
# terraform apply. Gives the charms time to apply any configuration
# changes declared in the action.
PRE_ACTION_PROPAGATION_DELAY_SEC = 5


def _run_action_on_unit(jhelper: JujuHelper, unit: str, action: str) -> dict | None:
    """Run a juju action on a unit, returning the result or None on failure."""
    try:
        return jhelper.run_action(unit, OPENSTACK_MODEL, action)
    except (ActionFailedException, Exception) as e:
        LOG.warning("action %s on %s failed: %s", action, unit, e)
        return None


def run_actions(
    jhelper: JujuHelper,
    actions: list[ActionSpec],
) -> PhaseResult:
    """Run a list of juju actions on the specified apps.

    For each action spec, resolves charm names to deployed app names
    (e.g. ``keystone-k8s`` → ``keystone``), then runs the action on
    the leader unit (or all units if scope=all-units). Returns failure
    on the first action that fails.

    :param jhelper: JujuHelper for the deployment
    :param actions: list of ActionSpec from metadata
    :returns: PhaseResult (success or failure with error code)
    """
    for spec in actions:
        # Resolve charm names to deployed app names
        status = jhelper.get_model_status(OPENSTACK_MODEL)
        apps = [name for name, app in status.apps.items() if app.charm in spec.apps]
        if not apps:
            LOG.info(
                "No deployed apps for charms %s — skipping action %s "
                "(feature not enabled)",
                spec.apps,
                spec.action,
            )
            continue

        for app in apps:
            if spec.scope == ActionScope.LEADER:
                try:
                    unit = jhelper.get_leader_unit(app, OPENSTACK_MODEL)
                except (LeaderNotFoundException, Exception) as e:
                    return PhaseResult(
                        success=False,
                        error_code=UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED,
                        error_message=(
                            f"Cannot find leader for {app} to run "
                            f"action {spec.action}: {e}"
                        ),
                    )
                if not unit:
                    return PhaseResult(
                        success=False,
                        error_code=UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED,
                        error_message=(
                            f"No leader unit for {app} to run action {spec.action}"
                        ),
                    )
                result = _run_action_on_unit(jhelper, unit, spec.action)
                if result is None:
                    return PhaseResult(
                        success=False,
                        error_code=UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED,
                        error_message=(f"Action {spec.action} failed on {unit}"),
                    )
            else:
                try:
                    app_status = jhelper.get_application(app, OPENSTACK_MODEL)
                except Exception as e:
                    return PhaseResult(
                        success=False,
                        error_code=UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED,
                        error_message=(
                            f"Cannot get units for {app} to run "
                            f"action {spec.action}: {e}"
                        ),
                    )
                for unit_name in app_status.units:
                    result = _run_action_on_unit(jhelper, unit_name, spec.action)
                    if result is None:
                        return PhaseResult(
                            success=False,
                            error_code=UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED,
                            error_message=(
                                f"Action {spec.action} failed on {unit_name}"
                            ),
                        )
    return PhaseResult(success=True)


def run_pre_actions(jhelper: JujuHelper, actions: list[ActionSpec]) -> PhaseResult:
    """Run pre-upgrade actions and wait for traefik propagation.

    :param jhelper: JujuHelper for the deployment
    :param actions: list of ActionSpec from group.pre_actions
    :returns: PhaseResult
    """
    if not actions:
        return PhaseResult(success=True)

    result = run_actions(jhelper, actions)
    if not result.success:
        return result

    LOG.info(
        "pre-upgrade actions complete, waiting %ds before proceeding",
        PRE_ACTION_PROPAGATION_DELAY_SEC,
    )
    time.sleep(PRE_ACTION_PROPAGATION_DELAY_SEC)
    return result


def run_post_actions(jhelper: JujuHelper, actions: list[ActionSpec]) -> PhaseResult:
    """Run post-upgrade actions (always runs, even after failure).

    :param jhelper: JujuHelper for the deployment
    :param actions: list of ActionSpec from group.post_actions
    :returns: PhaseResult
    """
    if not actions:
        return PhaseResult(success=True)

    result = run_actions(jhelper, actions)
    if not result.success:
        LOG.warning("post-upgrade action failed — manual intervention may be required.")
    return result
