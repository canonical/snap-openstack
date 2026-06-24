# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pytest
from click.testing import CliRunner

from sunbeam.core.common import ResultType, Role
from sunbeam.provider.local.commands import add, join, remove
from sunbeam.steps.clusterd import ClusterRemoveNodeStep
from sunbeam.steps.juju import JujuGrantModelAccessStep, RemoveJujuMachineStep
from sunbeam.steps.role_distributor import (
    ReapplyRoleDistributorApplicationStep,
    RemoveRoleDistributorUnitsStep,
)


@pytest.fixture()
def run_preflight():
    with patch("sunbeam.provider.local.commands.run_preflight_checks") as p:
        yield p


@pytest.fixture()
def daemon_group_check():
    with patch("sunbeam.provider.local.commands.DaemonGroupCheck") as p:
        yield p


@pytest.fixture()
def verify_fqdn_check():
    with patch("sunbeam.provider.local.commands.VerifyFQDNCheck") as p:
        yield p


@pytest.fixture()
def run_plan_cmd():
    with patch("sunbeam.provider.local.commands.run_plan") as p:
        yield p


@pytest.fixture()
def juju_helper_cmd():
    with patch("sunbeam.provider.local.commands.JujuHelper") as p:
        yield p


@pytest.fixture()
def get_step_result_cmd():
    with patch("sunbeam.provider.local.commands.get_step_result") as p:
        yield p


@pytest.fixture()
def get_step_message_cmd():
    with patch("sunbeam.provider.local.commands.get_step_message") as p:
        yield p


class TestAddNodeGrantModels:
    """Test that adding a node grants access to all Juju models dynamically."""

    def test_add_grants_access_to_all_models(
        self,
        daemon_group_check,
        verify_fqdn_check,
        run_preflight,
        run_plan_cmd,
        juju_helper_cmd,
        get_step_result_cmd,
        get_step_message_cmd,
    ):
        """run_plan receives JujuGrantModelAccessStep for every model."""
        jhelper_instance = juju_helper_cmd.return_value
        jhelper_instance.models.return_value = [
            {"short-name": "openstack-machines"},
            {"short-name": "openstack"},
            {"short-name": "observability"},
        ]

        add_node_result = Mock(result_type=ResultType.COMPLETED, message="test-token")
        create_user_result = Mock(result_type=ResultType.COMPLETED)
        get_step_result_cmd.side_effect = [add_node_result, create_user_result]
        get_step_message_cmd.return_value = "user-token"

        deployment = Mock()
        runner = CliRunner()
        result = runner.invoke(add, ["new-node.domain"], obj=deployment)

        assert result.exit_code == 0, result.output

        # Second call to run_plan is plan_access
        plan_access = run_plan_cmd.call_args_list[1][0][0]
        grant_steps = [
            s for s in plan_access if isinstance(s, JujuGrantModelAccessStep)
        ]
        assert len(grant_steps) == 3
        assert [s.model for s in grant_steps] == [
            "openstack-machines",
            "openstack",
            "observability",
        ]
        for step in grant_steps:
            assert step.username == "new-node.domain"

    def test_add_skips_models_with_empty_short_name(
        self,
        daemon_group_check,
        verify_fqdn_check,
        run_preflight,
        run_plan_cmd,
        juju_helper_cmd,
        get_step_result_cmd,
        get_step_message_cmd,
    ):
        """Models with empty short-name are excluded from grant steps."""
        jhelper_instance = juju_helper_cmd.return_value
        jhelper_instance.models.return_value = [
            {"short-name": "openstack"},
            {"short-name": "observability"},
            {"short-name": ""},
        ]

        add_node_result = Mock(result_type=ResultType.COMPLETED, message="test-token")
        create_user_result = Mock(result_type=ResultType.COMPLETED)
        get_step_result_cmd.side_effect = [add_node_result, create_user_result]
        get_step_message_cmd.return_value = "user-token"

        deployment = Mock()
        runner = CliRunner()
        result = runner.invoke(add, ["new-node.domain"], obj=deployment)

        assert result.exit_code == 0, result.output

        plan_access = run_plan_cmd.call_args_list[1][0][0]
        grant_steps = [
            s for s in plan_access if isinstance(s, JujuGrantModelAccessStep)
        ]
        assert len(grant_steps) == 2
        model_names = [s.model for s in grant_steps]
        assert "openstack" in model_names
        assert "observability" in model_names
        assert "" not in model_names


class TestJoinNodeValidation:
    """Test join validation behavior for gated roles."""

    @pytest.mark.parametrize(
        ("split_roles_in_cluster", "expected_exception", "expected_message"),
        [
            (
                False,
                click.ClickException,
                "feature\\.split-roles is not enabled in cluster state",
            ),
            (True, RuntimeError, "after-validation"),
        ],
    )
    @patch("sunbeam.provider.local.commands.read_config")
    @patch("sunbeam.provider.local.commands.DeploymentsConfig.load")
    @patch("sunbeam.provider.local.commands.deployment_path", return_value="/tmp")
    @patch("sunbeam.provider.local.commands.Snap")
    @patch("sunbeam.provider.local.commands.DaemonGroupCheck")
    @patch("sunbeam.provider.local.commands.utils.get_fqdn", return_value="node-2")
    @patch(
        "sunbeam.provider.local.commands._resolve_local_ip_from_cidr",
        return_value="10.0.0.2",
    )
    @patch(
        "sunbeam.provider.local.commands.utils.get_local_cidr_matching_token",
        return_value="10.0.0.0/24",
    )
    @patch("sunbeam.provider.local.commands.run_plan")
    @patch("sunbeam.provider.local.commands.run_preflight_checks")
    @patch("sunbeam.provider.local.commands.get_feature_gate_from_cluster")
    def test_join_validates_compute_and_network_after_cluster_join(
        self,
        _get_feature_gate_from_cluster,
        run_preflight_checks,
        run_plan,
        _get_local_cidr_matching_token,
        _resolve_local_ip_from_cidr,
        _daemon_group_check,
        _get_fqdn,
        snap_cls,
        _deployment_path,
        deployments_load,
        read_config,
        split_roles_in_cluster,
        expected_exception,
        expected_message,
    ):
        """Join should validate compute+network against cluster split-roles state."""
        deployment = Mock()
        deployment.get_client.return_value = Mock()
        deployment.juju_controller = Mock()
        deployment.juju_account = Mock()
        deployments_load.return_value = Mock()
        snap_cls.return_value.paths.user_data = "/tmp"
        _get_feature_gate_from_cluster.return_value = split_roles_in_cluster
        if split_roles_in_cluster:
            read_config.side_effect = RuntimeError("after-validation")

        ctx = click.Context(join, obj=deployment)
        with ctx, pytest.raises(expected_exception, match=expected_message):
            join.callback(
                token="token",
                roles=[Role.COMPUTE, Role.NETWORK],
                accept_defaults=False,
                show_hints=False,
                region_controller_token=None,
            )

        assert run_plan.call_count == 1
        first_plan = run_plan.call_args_list[0][0][0]
        assert first_plan[0].__class__.__name__ == "ClusterJoinNodeStep"


class TestRemoveNodeRoleDistributor:
    def test_remove_cleans_role_distributor_before_machine_removal_and_reapplies(
        self,
        daemon_group_check,
        run_preflight,
        run_plan_cmd,
        juju_helper_cmd,
    ):
        deployment = Mock()
        deployment.openstack_machines_model = "openstack-machines"
        deployment.get_manifest.return_value = Mock()
        deployment.get_tfhelper.return_value = Mock()
        deployment.get_ovn_manager.return_value.get_machines.return_value = ["1"]

        runner = CliRunner()
        result = runner.invoke(remove, ["node-1"], obj=deployment)

        assert result.exit_code == 0, result.output

        plan = run_plan_cmd.call_args_list[0][0][0]
        role_remove_idx = next(
            i
            for i, step in enumerate(plan)
            if isinstance(step, RemoveRoleDistributorUnitsStep)
        )
        juju_remove_idx = next(
            i for i, step in enumerate(plan) if isinstance(step, RemoveJujuMachineStep)
        )
        cluster_remove_idx = next(
            i for i, step in enumerate(plan) if isinstance(step, ClusterRemoveNodeStep)
        )
        role_reapply_idx = next(
            i
            for i, step in enumerate(plan)
            if isinstance(step, ReapplyRoleDistributorApplicationStep)
        )

        assert role_remove_idx < juju_remove_idx
        assert cluster_remove_idx < role_reapply_idx

    def test_remove_skips_role_distributor_when_microovn_has_no_machines(
        self,
        daemon_group_check,
        run_preflight,
        run_plan_cmd,
        juju_helper_cmd,
    ):
        deployment = Mock()
        deployment.openstack_machines_model = "openstack-machines"
        deployment.get_manifest.return_value = Mock()
        deployment.get_tfhelper.return_value = Mock()
        deployment.get_ovn_manager.return_value.get_machines.return_value = []

        runner = CliRunner()
        result = runner.invoke(remove, ["node-1"], obj=deployment)

        assert result.exit_code == 0, result.output

        plan = run_plan_cmd.call_args_list[0][0][0]
        assert not any(
            isinstance(step, RemoveRoleDistributorUnitsStep) for step in plan
        )
        assert not any(
            isinstance(step, ReapplyRoleDistributorApplicationStep) for step in plan
        )
