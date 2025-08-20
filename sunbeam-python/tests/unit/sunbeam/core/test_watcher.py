# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import Mock, call, patch

import pytest
import tenacity
from watcherclient.common.apiclient.exceptions import NotFound

import sunbeam.core.watcher as watcher_helper
from sunbeam.core.common import SunbeamException
from sunbeam.core.deployment import Deployment


@patch("sunbeam.core.watcher.read_config")
@patch("sunbeam.core.watcher.JujuHelper")
@patch("sunbeam.core.watcher.get_admin_connection")
@patch("sunbeam.core.watcher.watcher_client.Client")
def test_get_watcher_client(
    mock_watcher_client,
    mock_get_admin_connection,
    mock_jhelper,
    mock_read_config,
):
    mock_conn = Mock()
    mock_conn.session.get_endpoint.return_value = "fake_endpoint"
    mock_read_config.return_value = {"region": "fake_region"}
    mock_get_admin_connection.return_value = mock_conn
    controller = Mock()
    controller.name = "test"
    mock_deployment = Mock(spec=Deployment, juju_controller=controller)

    client = watcher_helper.get_watcher_client(mock_deployment)

    mock_read_config.assert_called_once_with(
        mock_deployment.get_client.return_value, "Region"
    )
    mock_get_admin_connection.assert_called_once_with(jhelper=mock_jhelper.return_value)

    mock_conn.session.get_endpoint.assert_called_once_with(
        service_type="infra-optim",
        region_name="fake_region",
    )
    mock_watcher_client.assert_called_once_with(
        session=mock_conn.session,
        endpoint="fake_endpoint",
    )
    assert client == mock_watcher_client.return_value


def test_create_host_maintenance_audit_template():
    mock_client = Mock()
    result = watcher_helper._create_host_maintenance_audit_template(mock_client)
    assert result == mock_client.audit_template.create.return_value
    mock_client.audit_template.create.assert_called_once_with(
        name="Sunbeam Cluster Maintaining Template",
        description="Audit template for cluster maintaining",
        goal="cluster_maintaining",
        strategy="host_maintenance",
    )


def test_create_workload_balancing_audit_template():
    mock_client = Mock()
    result = watcher_helper._create_workload_balancing_audit_template(mock_client)
    assert result == mock_client.audit_template.create.return_value
    mock_client.audit_template.create.assert_called_once_with(
        name="Sunbeam Cluster Workload Balancing Template",
        description="Audit template for workload balancing",
        goal="workload_balancing",
        strategy="workload_stabilization",
    )


def test_get_enable_maintenance_audit_template():
    mock_client = Mock()

    result = watcher_helper.get_enable_maintenance_audit_template(mock_client)
    assert result == mock_client.audit_template.get.return_value
    mock_client.audit_template.get.assert_called_once_with(
        "Sunbeam Cluster Maintaining Template"
    )


@patch("sunbeam.core.watcher._create_host_maintenance_audit_template")
def test_get_enable_maintenance_audit_template_not_found(mock_create_template_func):
    mock_client = Mock()
    mock_client.audit_template.get.side_effect = NotFound

    result = watcher_helper.get_enable_maintenance_audit_template(mock_client)
    assert result == mock_create_template_func.return_value
    mock_create_template_func.assert_called_once_with(client=mock_client)


def test_get_workload_balancing_audit_template():
    mock_client = Mock()

    result = watcher_helper.get_workload_balancing_audit_template(mock_client)
    assert result == mock_client.audit_template.get.return_value
    mock_client.audit_template.get.assert_called_once_with(
        "Sunbeam Cluster Workload Balancing Template"
    )


@patch("sunbeam.core.watcher._create_workload_balancing_audit_template")
def test_get_workload_balancing_audit_template_not_found(mock_create_template_func):
    mock_client = Mock()
    mock_client.audit_template.get.side_effect = NotFound

    result = watcher_helper.get_workload_balancing_audit_template(mock_client)
    assert result == mock_create_template_func.return_value
    mock_create_template_func.assert_called_once_with(client=mock_client)


@patch("sunbeam.core.watcher._wait_resource_in_target_state")
@patch("sunbeam.core.watcher._check_audit_plans_recommended")
def test_create_audit(
    mock_check_audit_plans_recommended, mock_wait_resource_in_target_state
):
    mock_client = Mock()
    mock_template = Mock()
    fake_audit_type = "fake_audit_type"
    fake_parameters = {"fake_parameter_a": "a", "fake_parameter_b": "b"}
    mock_audit = Mock()

    mock_client.audit.create.return_value = mock_audit

    mock_audit_detail = Mock()
    mock_audit_detail.state = "SUCCEEDED"
    mock_wait_resource_in_target_state.return_value = mock_audit_detail

    result = watcher_helper.create_audit(
        mock_client, mock_template, fake_audit_type, fake_parameters
    )

    assert result == mock_audit

    mock_client.audit.create.assert_called_once_with(
        audit_template_uuid=mock_template.uuid,
        audit_type=fake_audit_type,
        parameters=fake_parameters,
    )
    mock_wait_resource_in_target_state.assert_called_once_with(
        client=mock_client,
        resource_name="audit",
        resource_uuid=mock_audit.uuid,
    )
    mock_check_audit_plans_recommended.assert_called_once_with(
        client=mock_client, audit=mock_audit
    )


@patch("sunbeam.core.watcher._check_audit_plans_recommended")
def test_create_audit_failed(mock_check_audit_plan_recommended):
    mock_client = Mock()
    mock_template = Mock()
    fake_audit_type = "fake_audit_type"
    fake_parameters = {"fake_parameter_a": "a", "fake_parameter_b": "b"}
    mock_audit = Mock()
    mock_audit_detail = Mock()
    mock_audit_detail.state = "FAILED"

    mock_client.audit.create.return_value = mock_audit
    mock_client.audit.get.return_value = mock_audit_detail

    with pytest.raises(SunbeamException):
        watcher_helper.create_audit(
            mock_client, mock_template, fake_audit_type, fake_parameters
        )

    mock_client.audit.create.assert_called_once_with(
        audit_template_uuid=mock_template.uuid,
        audit_type=fake_audit_type,
        parameters=fake_parameters,
    )


def test_check_audit_plans_recommended():
    mock_client = Mock()
    mock_audit = Mock()
    mock_action_plans = [Mock(), Mock()]
    mock_action_plans[0].state = "RECOMMENDED"
    mock_action_plans[1].state = "SUCCEEDED"
    mock_client.action_plan.list.return_value = mock_action_plans

    watcher_helper._check_audit_plans_recommended(mock_client, mock_audit)
    mock_client.action_plan.list.assert_called_once_with(audit=mock_audit.uuid)


def test_check_audit_plans_recommended_failed():
    mock_client = Mock()
    mock_audit = Mock()
    mock_action_plans = [Mock(), Mock()]
    mock_action_plans[0].state = "RECOMMENDED"
    mock_action_plans[1].state = "FAILED"
    mock_client.action_plan.list.return_value = mock_action_plans

    with pytest.raises(SunbeamException):
        watcher_helper._check_audit_plans_recommended(mock_client, mock_audit)
    mock_client.action_plan.list.assert_called_once_with(audit=mock_audit.uuid)


def test_get_actions():
    mock_client = Mock()
    mock_audit = Mock()
    result = watcher_helper.get_actions(mock_client, mock_audit)
    assert result == mock_client.action.list.return_value
    mock_client.action.list.assert_called_once_with(
        audit=mock_audit.uuid,
        detail=True,
        limit=0,
        sort_key="created_at",
        sort_dir="asc",
    )


@patch("sunbeam.core.watcher._exec_plan")
def test_exec_audit(mock_exec_plan):
    mock_client = Mock()
    mock_audit = Mock()
    mock_action_plans = [Mock(), Mock()]
    mock_client.action_plan.list.return_value = mock_action_plans

    watcher_helper.exec_audit(mock_client, mock_audit)
    mock_client.action_plan.list.assert_called_once_with(audit=mock_audit.uuid)
    mock_exec_plan.assert_has_calls(
        [
            call(client=mock_client, action_plan=mock_action_plans[0]),
            call(client=mock_client, action_plan=mock_action_plans[1]),
        ]
    )


def test_exec_plan():
    mock_client = Mock()
    mock_action_plan = Mock()
    mock_action_plan.state = "PENDING"

    watcher_helper._exec_plan(mock_client, mock_action_plan)
    mock_client.action_plan.start.assert_called_once_with(
        action_plan_id=mock_action_plan.uuid
    )


def test_exec_plan_state_succeeded():
    mock_client = Mock()
    mock_action_plan = Mock()
    mock_action_plan.state = "SUCCEEDED"

    watcher_helper._exec_plan(mock_client, mock_action_plan)
    mock_client.action_plan.start.assert_not_called()


@patch("sunbeam.core.watcher._wait_resource_in_target_state.retry.sleep")
def test_wait_resource_in_target_state_pending(mock_func_sleep):
    mock_client = Mock()

    fake_resource = [Mock() for i in range(5)]
    fake_resource[-1].state = "SUCCEEDED"
    mock_client.fake_resource.get.side_effect = fake_resource

    watcher_helper._wait_resource_in_target_state(
        mock_client,
        "fake_resource",
        "fake-uuid",
    )
    mock_client.fake_resource.get.assert_has_calls(
        [call("fake-uuid") for _ in range(len(fake_resource))]
    )


@patch(
    "sunbeam.core.watcher._wait_resource_in_target_state.retry.stop",
    return_value=tenacity.stop_after_attempt(10),
)
@patch("sunbeam.core.watcher._wait_resource_in_target_state.retry.sleep")
def test_wait_resource_in_target_state_failed(mock_retry_sleep, mock_retry_stop):
    mock_client = Mock()

    fake_resource = [Mock() for i in range(5)]
    mock_client.fake_resource.get.side_effect = fake_resource

    with pytest.raises(SunbeamException):
        watcher_helper._wait_resource_in_target_state(
            mock_client,
            "fake_resource",
            "fake-uuid",
        )


@patch("sunbeam.core.watcher.get_actions")
@patch(
    "sunbeam.core.watcher.tenacity.wait_fixed",
    return_value=tenacity.wait_fixed(0),
)
@patch(
    "sunbeam.core.watcher.tenacity.stop_after_delay",
    return_value=tenacity.stop_after_attempt(10),
)
def test_wait_until_action_state(
    mock_stop_after_delay,
    mock_wait_fixed,
    mock_get_actions,
):
    mock_client = Mock()
    mock_status = Mock()
    mock_step = Mock()
    mock_step.status = "fake-status-msg"
    mock_audit = Mock()

    times = 5
    action_num = 3
    side_effect = []
    for i in range(times):
        actions = []
        for j in range(action_num):
            action = Mock()
            action.uuid = str(j)
            actions.append(action)
        side_effect.append(actions)
    for action in side_effect[-1]:
        action.state = "SUCCEEDED"

    side_effect[1][0].state = "SUCCEEDED"

    mock_get_actions.side_effect = side_effect

    watcher_helper.wait_until_action_state(
        mock_step, mock_audit, mock_client, mock_status
    )


@patch("sunbeam.core.watcher.get_actions")
@patch(
    "sunbeam.core.watcher.tenacity.wait_fixed",
    return_value=tenacity.wait_fixed(0),
)
@patch(
    "sunbeam.core.watcher.tenacity.stop_after_delay",
    return_value=tenacity.stop_after_attempt(10),
)
def test_wait_until_action_state_failed(
    mock_stop_after_delay,
    mock_wait_fixed,
    mock_get_actions,
):
    mock_client = Mock()
    mock_status = Mock()
    mock_step = Mock()
    mock_step.status = "fake-status-msg"
    mock_audit = Mock()

    mock_get_actions.return_value = [Mock()]
    mock_get_actions.return_value[0].state = "FAILED"

    with pytest.raises(watcher_helper.WatcherActionFailedException):
        watcher_helper.wait_until_action_state(
            mock_step, mock_audit, mock_client, mock_status
        )
