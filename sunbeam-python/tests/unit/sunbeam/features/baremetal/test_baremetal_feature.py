# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import ANY, Mock, call, patch

import pytest

from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.baremetal import constants, feature_config
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

    @patch.object(ironic_feature.commands, "_baremetal_resource_add")
    @patch("sunbeam.features.baremetal.commands.JujuHelper")
    @patch.object(ironic_feature, "JujuHelper")
    @patch.object(ironic_feature, "click", Mock())
    def test_run_enable_plans(
        self,
        mock_JujuHelper,
        commands_JujuHelper,
        mock_baremetal_resource_add,
        deployment,
    ):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        ironic._manifest.core.software.charms = {}

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
            additional_files=additional_files,
        )
        configfile_data = test_feature_config._get_generic_sample_config(
            "lish", "netmiko_arista_eos"
        )
        additional_files = {"lish-key": "lish"}
        valid_generic = feature_config._Config(
            configfile=configfile_data,
            additional_files=additional_files,
        )
        switchconfigs = feature_config._SwitchConfigs(
            netconf={"foo": valid_netconf},
            generic={"lish": valid_generic},
        )

        config = feature_config.BaremetalFeatureConfig(
            shards=["foo", "lish"],
            conductor_groups=["foo", "lish"],
            switchconfigs=switchconfigs,
        )
        jhelper = mock_JujuHelper.return_value
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
        expected_tfvars = {
            constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR: {
                "netconf": ["switch-config-foo"],
                "generic": ["switch-config-lish"],
            },
            constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR: "foo-id",
            constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR: "lish-id",
        }
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.write_tfvars(expected_tfvars)

        # RunSetTempUrlSecretStep calls.
        jhelper = commands_JujuHelper.return_value
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
            "foo": {"shard": "foo"},
            "lish": {"shard": "lish"},
        }
        mock_baremetal_resource_add.assert_any_call(
            ironic,
            deployment,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
            expected_items,
            "nova-ironic",
            replace=True,
        )

        # DeployIronicConductorGroupsStep call.
        expected_items = {
            "foo": {"conductor-group": "foo"},
            "lish": {"conductor-group": "lish"},
        }
        mock_baremetal_resource_add.assert_any_call(
            ironic,
            deployment,
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR,
            expected_items,
            "ironic-conductor",
            replace=True,
            apps_desired_status=["active", "blocked"],
        )

        app_names = [f"ironic-conductor-{name}" for name in ["foo", "lish"]]
        for app_name in app_names:
            jhelper.get_leader_unit.assert_any_call(app_name, OPENSTACK_MODEL)

        jhelper.wait_until_active.assert_any_call(
            OPENSTACK_MODEL,
            app_names,
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=ANY,
        )

    def test_set_tfvars_on_enable(self, deployment):
        ironic = ironic_feature.BaremetalFeature()
        feature_config = Mock()

        extra_tfvars = ironic.set_tfvars_on_enable(deployment, feature_config)

        expected_tfvars = {
            "enable-ironic": True,
            "enable-ceph-rgw": True,
        }
        assert extra_tfvars == expected_tfvars

    def test_set_tfvars_on_disable(self, deployment):
        ironic = ironic_feature.BaremetalFeature()

        extra_tfvars = ironic.set_tfvars_on_disable(deployment)

        expected_tfvars = {
            "enable-ironic": False,
            "enable-ceph-rgw": False,
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR: {},
            constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR: "",
            constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR: "",
        }
        assert extra_tfvars == expected_tfvars
