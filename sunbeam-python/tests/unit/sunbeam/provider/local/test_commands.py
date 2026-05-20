# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from sunbeam.core.common import ResultType
from sunbeam.provider.local.commands import add
from sunbeam.steps.juju import JujuGrantModelAccessStep


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
