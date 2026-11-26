# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import ANY, MagicMock, Mock, call, patch

import click
import pytest

from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.baremetal import constants, feature_config, steps
from sunbeam.features.baremetal import feature as ironic_feature
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from sunbeam.steps import openstack
from tests.unit.sunbeam.features.baremetal import test_feature_config


@pytest.fixture()
def deployment():
    deploy = Mock()
    deploy.openstack_machines_model = "foo"
    client = deploy.get_client.return_value
    client._openstack_terraform_vars = {}
    nodes = [
        {
            "name": "node1",
            "machineid": 1,
        },
    ]
    client.cluster.list_nodes_by_role.return_value = nodes
    client.cluster.list_nodes.return_value = nodes

    def get_config(key):
        if key == openstack.DATABASE_MEMORY_KEY:
            return "{}"
        elif key == OPENSTACK_TERRAFORM_VARS:
            return json.dumps(client._openstack_terraform_vars)

        return json.dumps(
            {
                "database": "multi",
            }
        )

    client.cluster.get_config.side_effect = get_config
    deploy.get_tfhelper.return_value.tfvar_map = {"charms": {}}

    yield deploy


class TestBaremetalFeature:
    def test_config_type(self):
        ironic = ironic_feature.BaremetalFeature()
        config_type = ironic.config_type()

        assert config_type == feature_config.BaremetalFeatureConfig

    def test_set_application_names(self, deployment):
        ironic = ironic_feature.BaremetalFeature()

        apps = ironic.set_application_names(deployment)

        expected_apps = [
            "ironic",
            "ironic-mysql-router",
            "nova-ironic",
            "nova-ironic-mysql-router",
            "ironic-conductor",
            "ironic-conductor-mysql-router",
            "neutron-baremetal-switch-config",
            "neutron-generic-switch-config",
            "ironic-mysql",
        ]
        assert expected_apps == apps

    @patch.object(steps.DeployIronicConductorGroupsStep, "_apply_tfvars")
    @patch.object(steps.DeployNovaIronicShardsStep, "_apply_tfvars")
    @patch.object(steps.UpdateSwitchConfigSecretsStep, "_apply_tfvars")
    @patch.object(steps, "JujuHelper")
    @patch.object(ironic_feature, "JujuHelper")
    @patch.object(ironic_feature, "click", Mock())
    @patch.object(ironic_feature, "run_preflight_checks")
    def test_run_enable_plans(
        self,
        mock_run_preflight_checks,
        mock_JujuHelper,
        mock_steps_JujuHelper,
        mock_switch_apply,
        mock_shards_apply,
        mock_conductors_apply,
        deployment,
    ):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        ironic._manifest.core.software.charms = {}

        # The INFRA_APPS gate plans against the openstack tfhelper
        deployment.get_tfhelper.side_effect = lambda plan: (
            Mock(
                plan=plan,
                tfvar_map={"charms": {}},
                plan_resource_changes=Mock(return_value={}),
            )
            if plan == "openstack-plan"
            else deployment.get_tfhelper.return_value
        )

        # set existing secrets.
        client = deployment.get_client.return_value
        client._openstack_terraform_vars = {
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {
                "netconf": ["switch-config-bar"],
                "generic": ["switch-config-tender"],
            }
        }

        configfile_data = test_feature_config._get_netconf_sample_config("foo")
        additional_files = {"foo-key": "foo"}
        valid_netconf = feature_config._Config(
            configfile=configfile_data,
        )
        valid_netconf.additional_files = additional_files
        configfile_data = test_feature_config._get_generic_sample_config(
            "lish", "netmiko_arista_eos"
        )
        additional_files = {"lish-key": "lish"}
        valid_generic = feature_config._Config(
            configfile=configfile_data,
        )
        valid_generic.additional_files = additional_files
        switchconfigs = feature_config._SwitchConfigs(
            netconf={"foo": valid_netconf},
            generic={"lish": valid_generic},
        )

        config = feature_config.BaremetalFeatureConfig(
            shards=["foo", "lish"],
            conductor_groups=["foo", "lish"],
            switchconfigs=switchconfigs,
        )
        jhelper = mock_steps_JujuHelper.return_value
        jhelper.add_secret.side_effect = ["foo-id", "lish-id"]

        # Run enable plans.
        ironic.run_enable_plans(deployment, config, False)

        # UpdateSwitchConfigSecretsStep calls.
        jhelper.remove_secret.assert_has_calls(
            [
                call(OPENSTACK_MODEL, "switch-config-bar"),
                call(OPENSTACK_MODEL, "switch-config-tender"),
            ]
        )
        jhelper.add_secret.assert_has_calls(
            [
                call(
                    OPENSTACK_MODEL,
                    "switch-config-foo",
                    {
                        "conf": valid_netconf.configfile,
                        "foo-key": "foo",
                    },
                    ANY,
                ),
                call(
                    OPENSTACK_MODEL,
                    "switch-config-lish",
                    {
                        "conf": valid_generic.configfile,
                        "lish-key": "lish",
                    },
                    ANY,
                ),
            ]
        )
        jhelper.grant_secret.assert_has_calls(
            [
                call(OPENSTACK_MODEL, "switch-config-foo", "neutron"),
                call(
                    OPENSTACK_MODEL,
                    "switch-config-foo",
                    "neutron-baremetal-switch-config",
                ),
                call(OPENSTACK_MODEL, "switch-config-lish", "neutron"),
                call(
                    OPENSTACK_MODEL,
                    "switch-config-lish",
                    "neutron-generic-switch-config",
                ),
            ]
        )
        expected_items = {
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {
                "netconf": ["switch-config-foo"],
                "generic": ["switch-config-lish"],
            },
            constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR: "foo-id",
            constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR: "lish-id",
        }
        mock_switch_apply.assert_any_call(
            expected_items,
            [
                "neutron",
                "neutron-baremetal-switch-config",
                "neutron-generic-switch-config",
            ],
            reporter=ANY,
        )

        # RunSetTempUrlSecretStep calls.
        jhelper = mock_JujuHelper.return_value
        jhelper.get_leader_unit.assert_any_call(
            constants.IRONIC_CONDUCTOR_APP,
            OPENSTACK_MODEL,
        )
        jhelper.wait_until_active.assert_any_call(
            OPENSTACK_MODEL,
            [constants.IRONIC_CONDUCTOR_APP],
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
        )

        # DeployNovaIronicShardsStep call.
        expected_items = {
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {
                "netconf": ["switch-config-bar"],
                "generic": ["switch-config-tender"],
            },
            constants.NOVA_IRONIC_SHARDS_TFVAR: {
                "foo": {"shard": "foo"},
                "lish": {"shard": "lish"},
            },
        }
        mock_shards_apply.assert_any_call(
            expected_items,
            ["nova-ironic-foo", "nova-ironic-lish"],
            reporter=ANY,
        )

        # DeployIronicConductorGroupsStep call.
        expected_items = {
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {
                "netconf": ["switch-config-bar"],
                "generic": ["switch-config-tender"],
            },
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR: {
                "foo": {"conductor-group": "foo"},
                "lish": {"conductor-group": "lish"},
            },
        }
        mock_conductors_apply.assert_any_call(
            expected_items,
            ["ironic-conductor-foo", "ironic-conductor-lish"],
            reporter=ANY,
        )

    def test_set_tfvars_on_enable(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        feature_config = Mock()

        extra_tfvars = ironic.set_tfvars_on_enable(deployment, feature_config)

        expected_tfvars = {
            "enable-ironic": True,
        }
        assert extra_tfvars == expected_tfvars

    def test_set_tfvars_on_disable(self, deployment):
        ironic = ironic_feature.BaremetalFeature()

        extra_tfvars = ironic.set_tfvars_on_disable(deployment)

        expected_tfvars = {
            "enable-ironic": False,
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR: {},
            constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR: "",
            constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR: "",
        }
        assert extra_tfvars == expected_tfvars


class TestStandaloneCommandsInfraGate:
    """Standalone baremetal commands run the INFRA_APPS gate.

    Commands that reapply the openstack plan are gated before persisting
    or applying anything.
    """

    @pytest.fixture()
    def deployment(self):
        deploy = MagicMock()
        deploy.get_manifest.return_value = MagicMock()
        yield deploy

    @staticmethod
    def _invoke(ironic, command, deployment, **params):
        """Invoke a feature command method with a click context."""
        with click.Context(command, obj=deployment):
            command.callback(ironic, **params)

    @patch.object(ironic_feature, "run_plan")
    @patch.object(steps, "JujuHelper")
    @patch.object(ironic_feature, "JujuHelper")
    @patch.object(ironic_feature, "run_preflight_checks")
    @patch.object(ironic_feature.feature_config._SwitchConfigs, "read_switch_config")
    @patch.object(ironic_feature.OpenStackControlPlaneFeature, "validate_infra_apps")
    def test_apply_commands_are_gated(
        self,
        mock_validate,
        mock_read_switch_config,
        mock_run_preflight_checks,
        mock_feature_jhelper,
        mock_steps_jhelper,
        mock_run_plan,
        deployment,
    ):
        switch_configs = MagicMock()
        switch_configs.netconf = {"foo": MagicMock()}
        switch_configs.generic = {}
        mock_read_switch_config.return_value = switch_configs

        ironic = ironic_feature.BaremetalFeature()

        gated_commands = [
            (ironic.compute_shard_add, {"shard": "foo"}),
            (ironic.compute_shard_delete, {"shard": "foo"}),
            (ironic.conductor_group_add, {"group_name": "foo"}),
            (ironic.conductor_group_delete, {"group_name": "foo"}),
            (
                ironic.switch_config_add,
                {
                    "protocol": "netconf",
                    "name": "foo",
                    "config": MagicMock(),
                    "additional_files": [],
                },
            ),
            (ironic.switch_config_delete, {"name": "foo"}),
        ]
        for command, params in gated_commands:
            self._invoke(ironic, command, deployment, show_hints=False, **params)

        assert mock_validate.call_count == len(gated_commands)
        for validate_args in mock_validate.call_args_list:
            assert validate_args.args[0] is deployment

        # The juju-secret-only command is not gated.
        self._invoke(
            ironic,
            ironic.switch_config_update,
            deployment,
            show_hints=False,
            protocol="netconf",
            name="foo",
            config=MagicMock(),
            additional_files=[],
        )
        assert mock_validate.call_count == len(gated_commands)
