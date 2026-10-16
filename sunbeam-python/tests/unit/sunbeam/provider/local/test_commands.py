# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import click
import pytest
from click.testing import CliRunner

from sunbeam.core.common import ResultType, Role
from sunbeam.provider.local.commands import add, join
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


class TestConfigureSRIOVInfraValidation:
    """The INFRA_APPS gate runs before sriov config and manifest storage.

    The gate only runs on the path that reapplies the openstack plan
    (PCI aliases in the manifest): the hypervisor-only path leaves
    INFRA_APPS alone and must not be blocked on unrelated drift.
    """

    def _deployment(self, pci_aliases=None):
        deployment = Mock()
        client = deployment.get_client.return_value
        client.cluster.get_node_info.return_value = {"role": ["compute"]}
        manifest = deployment.get_manifest.return_value
        if pci_aliases is None:
            manifest.core.config.pci = None
        else:
            manifest.core.config.pci = Mock(aliases=pci_aliases)
        deployment.get_tfhelper.side_effect = lambda plan: Mock()
        deployment.openstack_machines_model = "openstack-machines"
        return deployment

    @patch("sunbeam.provider.local.commands.retrieve_admin_credentials")
    @patch("sunbeam.provider.local.commands.run_preflight_checks")
    @patch("sunbeam.provider.local.commands.utils.get_fqdn", return_value="node-2")
    @patch("sunbeam.provider.local.commands.run_plan")
    def test_gate_precedes_config_and_manifest_storage(
        self, run_plan, _fqdn, _preflight, _creds
    ):
        from sunbeam.provider.local.commands import configure_sriov
        from sunbeam.steps.openstack import ValidateInfraAppsStep

        deployment = self._deployment(pci_aliases=["0000:00:05.0"])
        manifest_path = Mock()
        ctx = click.Context(configure_sriov, obj=deployment)
        with ctx:
            configure_sriov.callback(
                manifest_path=manifest_path,
                accept_defaults=False,
                show_hints=False,
                clear_previous_config=False,
            )
        # Order: gate, manifest storage, then the sriov config plan.
        assert run_plan.call_count == 3
        gate_plan = run_plan.call_args_list[0][0][0]
        assert gate_plan[0].__class__.__name__ == "TerraformInitStep"
        assert isinstance(gate_plan[1], ValidateInfraAppsStep)
        storage_plan = run_plan.call_args_list[1][0][0]
        assert storage_plan[0].__class__.__name__ == "AddManifestStep"
        config_plan = run_plan.call_args_list[2][0][0]
        assert config_plan[0].__class__.__name__ == "LocalConfigSRIOVStep"

    @patch("sunbeam.provider.local.commands.retrieve_admin_credentials")
    @patch("sunbeam.provider.local.commands.run_preflight_checks")
    @patch("sunbeam.provider.local.commands.utils.get_fqdn", return_value="node-2")
    @patch("sunbeam.provider.local.commands.run_plan")
    def test_hypervisor_only_path_is_not_gated(
        self, run_plan, _fqdn, _preflight, _creds
    ):
        """Without PCI aliases the openstack plan is not reapplied: no gate.

        Unrelated INFRA_APP drift must not block configuring a compute
        node whose operation leaves those applications unchanged.
        """
        from sunbeam.provider.local.commands import configure_sriov
        from sunbeam.steps.openstack import ValidateInfraAppsStep

        deployment = self._deployment()
        ctx = click.Context(configure_sriov, obj=deployment)
        with ctx:
            configure_sriov.callback(
                manifest_path=Mock(),
                accept_defaults=False,
                show_hints=False,
                clear_previous_config=False,
            )
        # Order: manifest storage, then the sriov config plan. No gate.
        assert run_plan.call_count == 2
        for call in run_plan.call_args_list:
            assert not any(
                isinstance(step, ValidateInfraAppsStep) for step in call.args[0]
            )
        storage_plan = run_plan.call_args_list[0][0][0]
        assert storage_plan[0].__class__.__name__ == "AddManifestStep"
        config_plan = run_plan.call_args_list[1][0][0]
        assert config_plan[0].__class__.__name__ == "LocalConfigSRIOVStep"

    @patch("sunbeam.provider.local.commands.LocalConfigSRIOVStep")
    @patch("sunbeam.provider.local.commands.AddManifestStep")
    @patch("sunbeam.provider.local.commands.retrieve_admin_credentials")
    @patch("sunbeam.provider.local.commands.run_preflight_checks")
    @patch("sunbeam.provider.local.commands.utils.get_fqdn", return_value="node-2")
    def test_gate_failure_skips_manifest_storage_and_config(
        self, _fqdn, _preflight, _creds, add_manifest_step, local_config_step
    ):
        """A failed gate must not store the manifest or configure sriov."""
        import click as _click

        from sunbeam.core.common import Result
        from sunbeam.provider.local.commands import configure_sriov

        deployment = self._deployment(pci_aliases=["0000:00:05.0"])
        ctx = click.Context(configure_sriov, obj=deployment)
        with (
            ctx,
            patch(
                "sunbeam.steps.openstack.ValidateInfraAppsStep.run",
                return_value=Result(ResultType.FAILED, "pending infra changes"),
            ),
        ):
            with pytest.raises(_click.ClickException, match="pending infra"):
                configure_sriov.callback(
                    manifest_path=Mock(),
                    accept_defaults=False,
                    show_hints=False,
                    clear_previous_config=False,
                )
        # The drifted manifest was never stored and nothing was
        # configured on the node.
        add_manifest_step.assert_not_called()
        local_config_step.assert_not_called()
