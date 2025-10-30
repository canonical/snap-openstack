# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import ANY, Mock, patch

import pytest

from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.baremetal import constants, feature_config
from sunbeam.features.baremetal import feature as ironic_feature
from sunbeam.steps import openstack


@pytest.fixture()
def deployment():
    deploy = Mock()
    deploy.openstack_machines_model = "foo"
    client = deploy.get_client.return_value
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
            "ironic-mysql",
        ]
        assert expected_apps == apps

    @patch.object(ironic_feature.commands, "_baremetal_resource_add")
    @patch.object(ironic_feature, "JujuHelper")
    @patch.object(ironic_feature, "click", Mock())
    def test_run_enable_plans(
        self, mock_JujuHelper, mock_baremetal_resource_add, deployment
    ):
        ironic = ironic_feature.BaremetalFeature()
        ironic._manifest = Mock()
        ironic._manifest.core.software.charms = {}
        config = feature_config.BaremetalFeatureConfig(
            shards=["foo", "lish"],
        )

        # Run enable plans.
        ironic.run_enable_plans(deployment, config, False)

        # RunSetTempUrlSecretStep calls.
        jhelper = mock_JujuHelper.return_value
        jhelper.get_leader_unit.assert_called_once_with(
            constants.IRONIC_CONDUCTOR_APP,
            OPENSTACK_MODEL,
        )
        jhelper.wait_until_active.assert_called_once_with(
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
        mock_baremetal_resource_add.assert_called_once_with(
            ironic,
            deployment,
            constants.NOVA_IRONIC_SHARDS_TFVAR,
            expected_items,
            "nova-ironic",
            replace=True,
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
        }
        assert extra_tfvars == expected_tfvars
