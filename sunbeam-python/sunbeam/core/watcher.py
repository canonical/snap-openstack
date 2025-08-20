# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import TYPE_CHECKING, Any, Iterable

import tenacity
from rich.status import Status

from sunbeam.core.common import BaseStep, SunbeamException, read_config
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.openstack import REGION_CONFIG_KEY
from sunbeam.core.openstack_api import get_admin_connection
from sunbeam.lazy import LazyImport

if TYPE_CHECKING:
    from watcherclient import v1 as watcher
    from watcherclient.common.apiclient import exceptions as watcher_exceptions
    from watcherclient.v1 import client as watcher_client
else:
    watcher = LazyImport("watcherclient.v1")
    watcher_client = LazyImport("watcherclient.v1.client")
    watcher_exceptions = LazyImport("watcherclient.common.apiclient.exceptions")

LOG = logging.getLogger(__name__)

WATCHER_APPLICATION = "watcher"

# Timeout of seconds while waiting for the watcher resource to reach the target state.
WAIT_TIMEOUT = 60 * 3
# Sleep interval (in seconds) between querying watcher resources.
WAIT_SLEEP_INTERVAL = 5
ENABLE_MAINTENANCE_AUDIT_TEMPLATE_NAME = "Sunbeam Cluster Maintaining Template"
ENABLE_MAINTENANCE_STRATEGY_NAME = "host_maintenance"
ENABLE_MAINTENANCE_GOAL_NAME = "cluster_maintaining"

WORKLOAD_BALANCING_GOAL_NAME = "workload_balancing"
WORKLOAD_BALANCING_STRATEGY_NAME = "workload_stabilization"
WORKLOAD_BALANCING_AUDIT_TEMPLATE_NAME = "Sunbeam Cluster Workload Balancing Template"


class WatcherActionFailedException(Exception):
    """Raise if Watcher Action in FAILED state."""

    pass


def get_watcher_client(deployment: Deployment) -> "watcher_client.Client":
    region = read_config(deployment.get_client(), REGION_CONFIG_KEY)["region"]
    conn = get_admin_connection(jhelper=JujuHelper(deployment.juju_controller))

    watcher_endpoint = conn.session.get_endpoint(
        service_type="infra-optim",
        region_name=region,
    )
    return watcher_client.Client(session=conn.session, endpoint=watcher_endpoint)


def _create_host_maintenance_audit_template(
    client: "watcher_client.Client",
) -> "watcher.AuditTemplate":
    template = client.audit_template.create(
        name=ENABLE_MAINTENANCE_AUDIT_TEMPLATE_NAME,
        description="Audit template for cluster maintaining",
        goal=ENABLE_MAINTENANCE_GOAL_NAME,
        strategy=ENABLE_MAINTENANCE_STRATEGY_NAME,
    )
    return template


def _create_workload_balancing_audit_template(
    client: "watcher_client.Client",
) -> "watcher.AuditTemplate":
    template = client.audit_template.create(
        name=WORKLOAD_BALANCING_AUDIT_TEMPLATE_NAME,
        description="Audit template for workload balancing",
        goal=WORKLOAD_BALANCING_GOAL_NAME,
        strategy=WORKLOAD_BALANCING_STRATEGY_NAME,
    )
    return template


def get_enable_maintenance_audit_template(
    client: "watcher_client.Client",
) -> "watcher.AuditTemplate":
    try:
        template = client.audit_template.get(ENABLE_MAINTENANCE_AUDIT_TEMPLATE_NAME)
    except watcher_exceptions.NotFound:
        template = _create_host_maintenance_audit_template(client=client)
    return template


def get_workload_balancing_audit_template(
    client: "watcher_client.Client",
) -> "watcher.AuditTemplate":
    try:
        template = client.audit_template.get(WORKLOAD_BALANCING_AUDIT_TEMPLATE_NAME)
    except watcher_exceptions.NotFound:
        template = _create_workload_balancing_audit_template(client=client)
    return template


@tenacity.retry(
    reraise=True,
    stop=tenacity.stop_after_delay(WAIT_TIMEOUT),
    wait=tenacity.wait_fixed(WAIT_SLEEP_INTERVAL),
)
def _wait_resource_in_target_state(
    client: "watcher_client.Client",
    resource_name: str,
    resource_uuid: str,
    states: list[str] = ["SUCCEEDED", "FAILED"],
) -> "watcher.Audit":
    src = getattr(client, resource_name).get(resource_uuid)
    if src.state not in states:
        raise SunbeamException(f"{resource_name} {resource_uuid} not in target state")
    return src


def create_audit(
    client: "watcher_client.Client",
    template: "watcher.AuditTemplate",
    audit_type: str = "ONESHOT",
    parameters: dict[str, Any] = {},
) -> "watcher.Audit":
    audit = client.audit.create(
        audit_template_uuid=template.uuid,
        audit_type=audit_type,
        parameters=parameters,
    )
    audit_details = _wait_resource_in_target_state(
        client=client,
        resource_name="audit",
        resource_uuid=audit.uuid,
    )

    if audit_details.state == "SUCCEEDED":
        LOG.debug(f"Create Watcher audit {audit.uuid} successfully")
    else:
        LOG.debug(f"Create Watcher audit {audit.uuid} failed")
        raise SunbeamException(
            f"Create watcher audit failed, template: {template.name}"
        )

    _check_audit_plans_recommended(client=client, audit=audit)
    return audit


def _check_audit_plans_recommended(
    client: "watcher_client.Client", audit: "watcher.Audit"
):
    action_plans = client.action_plan.list(audit=audit.uuid)
    # Verify all the action_plan's state is RECOMMENDED.
    # In case there is not action been generated, the action plan state
    # will be SUCCEEDED at the beginning.
    if not all(plan.state in ["RECOMMENDED", "SUCCEEDED"] for plan in action_plans):
        raise SunbeamException(
            f"Not all action plan for audit({audit.uuid}) is RECOMMENDED"
        )


def get_actions(
    client: "watcher_client.Client",
    audit: "watcher.Audit",
    limit: int = 0,
    sort_key: str = "created_at",
    sort_dir: str = "asc",
) -> list["watcher.Action"]:
    """Get list of actions by audit."""
    return client.action.list(
        audit=audit.uuid, detail=True, limit=limit, sort_key=sort_key, sort_dir=sort_dir
    )


def exec_audit(client: "watcher_client.Client", audit: "watcher.Audit"):
    """Run audit's action plans."""
    action_plans = client.action_plan.list(audit=audit.uuid)
    for action_plan in action_plans:
        _exec_plan(client=client, action_plan=action_plan)
    LOG.debug(f"All Action plans for Audit {audit.uuid} started")


def _exec_plan(client: "watcher_client.Client", action_plan: "watcher.ActionPlan"):
    """Run action plan."""
    if action_plan.state == "SUCCEEDED":
        LOG.debug(f"action plan {action_plan.uuid} state is SUCCEEDED, skip execution")
        return
    client.action_plan.start(action_plan_id=action_plan.uuid)
    LOG.debug(f"Start Watcher action plan {action_plan.uuid}")


def wait_until_action_state(
    step: BaseStep,
    audit: "watcher.Audit",
    client: "watcher_client.Client",
    status: Status | None,
    expected_state: Iterable[str] = ["SUCCEEDED"],
):
    actions = get_actions(client=client, audit=audit)
    nb_completed_actions = 0
    nb_actions = len(actions)
    completed_actions = dict.fromkeys([action.uuid for action in actions], False)

    message = (
        step.status
        + "waiting for actions to become expected state"
        + " ({nb_completed_actions}/{nb_actions})"
    )

    timeout = WAIT_TIMEOUT * nb_actions
    for attempt in tenacity.Retrying(
        stop=tenacity.stop_after_delay(timeout),
        wait=tenacity.wait_fixed(WAIT_SLEEP_INTERVAL),
        retry=tenacity.retry_if_not_exception_type(WatcherActionFailedException),
        reraise=True,
    ):
        with attempt:
            actions = get_actions(client=client, audit=audit)
            for action in actions:
                if completed_actions.get(action.uuid) is True:
                    continue
                LOG.debug(f"Watcher action {action.uuid} is in {action.state} state.")
                if action.state == "FAILED":
                    raise WatcherActionFailedException
                if action.state in expected_state:
                    completed_actions[action.uuid] = True
                    nb_completed_actions = sum(completed_actions.values())
                    if status is not None:
                        status.update(
                            message.format(
                                nb_completed_actions=nb_completed_actions,
                                nb_actions=nb_actions,
                            )
                        )
            if nb_completed_actions < nb_actions:
                raise SunbeamException(
                    "Not all actions completed: {}".format(
                        ",".join(
                            [
                                action.uuid
                                for action in actions
                                if action.state not in expected_state
                            ]
                        )
                    )
                )
            LOG.debug(
                f"All actions for Watcher audit {audit.uuid}"
                " have been successfully completed."
            )
