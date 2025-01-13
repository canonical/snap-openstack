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
from unittest.mock import Mock, call, patch

import pytest
from click.testing import CliRunner

from sunbeam.core.common import ResultType
from sunbeam.provider.maintenance.maintenance import (
    _get_node_roles,
    console,
    disable_maintenance,
    enable_maintenance,
)


@pytest.mark.parametrize(
    "deployment_type,nodes,status,expected",
    [
        (
            "local",
            ["node-1", "node-2"],
            {
                1: {"hostname": "node-1", "status": ["control"]},
                2: {"hostname": "node-2", "status": ["control"]},
            },
            (["node-1", "node-2"], [], []),
        ),
        (
            "maas",
            ["node-1"],
            {
                1: {"hostname": "node-1", "status": ["control"]},
            },
            (["node-1"], [], []),
        ),
        (
            "local",
            ["node-1", "node-2", "node-3"],
            {
                1: {"hostname": "node-1", "status": ["control", "storage"]},
                2: {"hostname": "node-2", "status": ["control", "compute"]},
                3: {"hostname": "node-3", "status": ["control", "compute", "storage"]},
                4: {"hostname": "node-4", "status": ["control", "compute", "storage"]},
            },
            (
                ["node-1", "node-2", "node-3"],
                ["node-2", "node-3"],
                ["node-1", "node-3"],
            ),
        ),
    ],
)
@patch("sunbeam.provider.maintenance.maintenance.LocalClusterStatusStep")
@patch("sunbeam.provider.maintenance.maintenance.MaasClusterStatusStep")
@patch("sunbeam.provider.maintenance.maintenance.run_plan")
@patch("sunbeam.provider.maintenance.maintenance.get_step_message")
def test_get_node_roles(
    mock_get_step_message,
    mock_run_plan,
    mock_maas_cluster_status_step,
    mock_local_cluster_status_step,
    deployment_type,
    nodes,
    status,
    expected,
):
    mock_deployment = Mock()
    mock_jhelper = Mock()
    mock_console = Mock()

    mock_deployment.type = deployment_type
    if deployment_type == "local":
        step = mock_local_cluster_status_step
    else:
        step = mock_maas_cluster_status_step

    mock_get_step_message.return_value = {"controller": status}

    result = _get_node_roles(mock_deployment, mock_jhelper, mock_console, False, nodes)

    mock_run_plan.assert_called_with(
        [step(mock_deployment, mock_jhelper)], mock_console, False
    )
    mock_get_step_message.assert_called_with(mock_run_plan.return_value, step)
    assert result == expected


@patch("sunbeam.provider.maintenance.maintenance.get_step_result")
@patch("sunbeam.provider.maintenance.maintenance.RunWatcherHostMaintenanceStep")
@patch("sunbeam.provider.maintenance.maintenance.run_preflight_checks")
@patch("sunbeam.provider.maintenance.maintenance.run_plan")
@patch("sunbeam.provider.maintenance.maintenance.JujuHelper")
@patch("sunbeam.provider.maintenance.maintenance.checks")
@patch("sunbeam.provider.maintenance.maintenance._get_node_roles")
def test_enable_maintenance(
    mock_get_node_roles,
    mock_checks,
    mock_jhelper,
    mock_run_plan,
    mock_run_preflight_checks,
    mock_run_watcher_host_maintenance_step,
    mock_get_step_result,
):
    runner = CliRunner()

    compute_nodes = ["node-1", "node-2"]
    mock_deployment = Mock()
    mock_get_node_roles.return_value = (
        [],
        compute_nodes,
        [],
    )
    mock_get_step_result.return_value.result_type = ResultType.COMPLETED

    steps = [
        mock_run_watcher_host_maintenance_step,
        mock_checks.NovaInDisableStatusCheck,
        mock_checks.NoInstancesOnNodeCheck,
    ]
    side_effects = [[Mock() for _ in compute_nodes] for _ in steps]
    for step, side_effect in zip(steps, side_effects):
        step.side_effect = side_effect

    result = runner.invoke(
        enable_maintenance,
        ["--nodes", ",".join(compute_nodes)],
        obj=mock_deployment,
    )
    assert result.exit_code == 0

    # Verify step are been called
    mock_run_plan.assert_has_calls(
        call(
            [side_effect],
            console,
            True,
        )
        for side_effect in side_effects[0]
    )
    mock_run_watcher_host_maintenance_step.assert_has_calls(
        [call(deployment=mock_deployment, node=node) for node in compute_nodes],
        any_order=True,
    )

    # Verify prefligh checks are been called
    preflight_checks_calls = [
        call(
            [
                mock_checks.NodeisNotControlRoleCheck.return_value,
                mock_checks.InstancesStatusCheck.return_value,
                mock_checks.NoEphemeralDiskCheck.return_value,
            ],
            console,
        ),
    ]
    preflight_checks_calls += [
        call(
            list(side_effect),
            console,
        )
        for side_effect in zip(side_effects[1], side_effects[2])
    ]
    mock_run_preflight_checks.assert_has_calls(preflight_checks_calls)
    mock_checks.NodeisNotControlRoleCheck.assert_called_with(nodes=[], force=False)
    mock_checks.InstancesStatusCheck.assert_called_with(
        jhelper=mock_jhelper.return_value, nodes=compute_nodes, force=False
    )
    mock_checks.NoEphemeralDiskCheck.assert_called_with(
        jhelper=mock_jhelper.return_value, nodes=compute_nodes, force=False
    )
    mock_checks.NovaInDisableStatusCheck.assert_has_calls(
        [
            call(jhelper=mock_jhelper.return_value, node=node, force=False)
            for node in compute_nodes
        ],
        any_order=True,
    )
    mock_checks.NoInstancesOnNodeCheck.assert_has_calls(
        [
            call(jhelper=mock_jhelper.return_value, node=node, force=False)
            for node in compute_nodes
        ],
        any_order=True,
    )


@patch("sunbeam.provider.maintenance.maintenance.RunWatcherWorkloadBalancingStep")
@patch("sunbeam.provider.maintenance.maintenance.EnableHypervisorStep")
@patch("sunbeam.provider.maintenance.maintenance.JujuHelper")
@patch("sunbeam.provider.maintenance.maintenance.run_plan")
@patch("sunbeam.provider.maintenance.maintenance._get_node_roles")
def test_disable_maintenance(
    mock_get_node_roles,
    mock_run_plan,
    mock_jhelper,
    mock_enable_hypervisor_step,
    mock_run_watcher_workload_balancing_step,
):
    runner = CliRunner()
    mock_deployment = Mock()
    compute_nodes = ["node-1", "node-2", "node-3"]

    mock_get_node_roles.return_value = (
        [],
        compute_nodes,
        [],
    )

    steps = [mock_enable_hypervisor_step, mock_run_watcher_workload_balancing_step]
    side_effects = [[Mock() for _ in compute_nodes] for _ in steps]
    for step, side_effect in zip(steps, side_effects):
        step.side_effect = side_effect

    result = runner.invoke(
        disable_maintenance,
        ["--nodes", ",".join(compute_nodes)],
        obj=mock_deployment,
    )
    assert result.exit_code == 0

    mock_run_plan.assert_has_calls(
        [
            call(
                [
                    side_effect[0],  # EnableHypervisorStep
                    side_effect[1],  # RunWatcherWorkloadBalancingStep
                ],
                console,
                True,
            )
            for side_effect in zip(side_effects[0], side_effects[1])
        ]
    )
    mock_enable_hypervisor_step.assert_has_calls(
        [
            call(
                client=mock_deployment.get_client.return_value,
                node=node,
                jhelper=mock_jhelper.return_value,
                model=mock_deployment.openstack_machines_model,
            )
            for node in compute_nodes
        ],
        any_order=True,
    )
    mock_run_watcher_workload_balancing_step.assert_has_calls(
        [call(deployment=mock_deployment) for _ in compute_nodes],
        any_order=True,
    )
