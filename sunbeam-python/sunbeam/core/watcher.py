# Copyright (c) 2024 Canonical Ltd.
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
import time
from typing import Any

import timeout_decorator
from watcherclient import v1 as watcher
from watcherclient.common.apiclient.exceptions import NotFound
from watcherclient.v1 import client as watcher_client

from sunbeam.core.common import SunbeamException, read_config
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.openstack_api import get_admin_connection
from sunbeam.steps.openstack import REGION_CONFIG_KEY

LOG = logging.getLogger(__name__)

# Timeout while waiting for the watcher resource to reach the target state.
TIMEOUT = 60 * 3
# Sleep interval between querying watcher resources.
SLEEP_INTERVAL = 5
ENABLE_MAINTENANCE_AUDIT_TEMPLATE_NAME = "Sunbeam Cluster Maintaining Template"
ENABLE_MAINTENANCE_STRATEGY_NAME = "host_maintenance"
ENABLE_MAINTENANCE_GOAL_NAME = "cluster_maintaining"

WORKLOAD_BALANCING_GOAL_NAME = "workload_balancing"
WORKLOAD_BALANCING_STRATEGY_NAME = "workload_stabilization"
WORKLOAD_BALANCING_AUDIT_TEMPLATE_NAME = "Sunbeam Cluster Workload Balancing Template"


def get_watcher_client(deployment: Deployment) -> watcher_client.Client:
    region = read_config(deployment.get_client(), REGION_CONFIG_KEY)["region"]
    conn = get_admin_connection(
        jhelper=JujuHelper(deployment.get_connected_controller())
    )

    watcher_endpoint = conn.session.get_endpoint(
        service_type="infra-optim",
        region_name=region,
    )
    return watcher_client.Client(session=conn.session, endpoint=watcher_endpoint)


def _create_host_maintenance_audit_template(
    client: watcher_client.Client,
) -> watcher.AuditTemplate:
    template = client.audit_template.create(
        name=ENABLE_MAINTENANCE_AUDIT_TEMPLATE_NAME,
        description="Audit template for cluster maintaining",
        goal=ENABLE_MAINTENANCE_GOAL_NAME,
        strategy=ENABLE_MAINTENANCE_STRATEGY_NAME,
    )
    return template


def _create_workload_balancing_audit_template(
    client: watcher_client.Client,
) -> watcher.AuditTemplate:
    template = client.audit_template.create(
        name=WORKLOAD_BALANCING_AUDIT_TEMPLATE_NAME,
        description="Audit template for workload balancing",
        goal=WORKLOAD_BALANCING_GOAL_NAME,
        strategy=WORKLOAD_BALANCING_STRATEGY_NAME,
    )
    return template


def get_enable_maintenance_audit_template(
    client: watcher_client.Client,
) -> watcher.AuditTemplate:
    try:
        template = client.audit_template.get(ENABLE_MAINTENANCE_AUDIT_TEMPLATE_NAME)
    except NotFound:
        template = _create_host_maintenance_audit_template(client=client)
    return template


def get_workload_balancing_audit_template(
    client: watcher_client.Client,
) -> watcher.AuditTemplate:
    try:
        template = client.audit_template.get(WORKLOAD_BALANCING_AUDIT_TEMPLATE_NAME)
    except NotFound:
        template = _create_workload_balancing_audit_template(client=client)
    return template


@timeout_decorator.timeout(TIMEOUT)
def create_audit(
    client: watcher_client.Client,
    template: watcher.AuditTemplate,
    audit_type: str = "ONESHOT",
    parameters: dict[str, Any] = {},
) -> watcher.Audit:
    audit = client.audit.create(
        audit_template_uuid=template.uuid,
        audit_type=audit_type,
        parameters=parameters,
    )
    while True:
        audit_details = client.audit.get(audit.uuid)
        if audit_details.state in ["SUCCEEDED", "FAILED"]:
            break
        time.sleep(SLEEP_INTERVAL)
    if audit_details.state == "SUCCEEDED":
        LOG.debug(f"Create Watcher audit {audit.uuid} successfully")
    else:
        LOG.debug(f"Create Watcher audit {audit.uuid} failed")
        raise SunbeamException(
            f"Create watcher audit failed, template: {template.name}"
        )

    _check_audit_plans_recommended(client=client, audit=audit)
    return audit


def _check_audit_plans_recommended(client: watcher_client.Client, audit: watcher.Audit):
    action_plans = client.action_plan.list(audit=audit.uuid)
    # Verify all the action_plan's state is RECOMMENDED
    if not all(plan.state in ["RECOMMENDED", "SUCCEEDED"] for plan in action_plans):
        raise SunbeamException(
            f"Not all action plan for audit({audit.uuid}) is RECOMMENDED"
        )


def get_actions(
    client: watcher_client.Client, audit: watcher.Audit
) -> list[watcher.Action]:
    """Get list of actions by audit."""
    return client.action.list(audit=audit.uuid, detail=True)


def exec_audit(client: watcher_client.Client, audit: watcher.Audit):
    """Run audit's action plans."""
    action_plans = client.action_plan.list(audit=audit.uuid)
    for action_plan in action_plans:
        _exec_plan(client=client, action_plan=action_plan)
    LOG.info(f"All Action plan for Audit {audit.uuid} execution successfully")


@timeout_decorator.timeout(TIMEOUT)
def _exec_plan(client: watcher_client.Client, action_plan: watcher.ActionPlan):
    """Run action plan."""
    if action_plan.state == "SUCCEEDED":
        LOG.debug(f"action plan {action_plan.uuid} state is SUCCEEDED, skip execution")
        return
    client.action_plan.start(action_plan_id=action_plan.uuid)

    _action_plan: watcher.ActionPlan
    while True:
        _action_plan = client.action_plan.get(action_plan_id=action_plan.uuid)
        if _action_plan.state in ["SUCCEEDED", "FAILED"]:
            break
        time.sleep(SLEEP_INTERVAL)

    if _action_plan.state == "SUCCEEDED":
        LOG.debug(f"Action plan {action_plan.uuid} execution successfully")
    else:
        LOG.debug(f"Action plan {action_plan.uuid} execution failed")
        raise SunbeamException(f"Action plan {action_plan.uuid} execution failed")
