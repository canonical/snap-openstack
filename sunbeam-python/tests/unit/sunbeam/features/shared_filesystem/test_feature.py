# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import ANY, Mock, patch

import pytest

from sunbeam.features.shared_filesystem import feature as manila_feature
from sunbeam.features.shared_filesystem import manila_data
from sunbeam.steps import microceph, openstack


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
                "horizon-plugins": ["foo"],
            }
        )

    client.cluster.get_config.side_effect = get_config

    yield deploy


class TestSharedFilesystemFeature:
    def test_set_application_names(self, deployment):
        manila = manila_feature.SharedFilesystemFeature()

        apps = manila.set_application_names(deployment)

        expected_apps = [
            "manila",
            "manila-mysql-router",
            "manila-cephfs",
            "manila-cephfs-mysql-router",
            "manila-data-mysql-router",
            "manila-mysql",
        ]
        assert expected_apps == apps

    @patch.object(manila_feature, "JujuHelper")
    @patch.object(manila_feature, "click", Mock())
    def test_run_enable_plans(self, mock_JujuHelper, deployment):
        jhelper = mock_JujuHelper.return_value
        manila = manila_feature.SharedFilesystemFeature()
        manila._manifest = Mock()
        manila._manifest.core.software.charms = {}
        feature_config = Mock()

        # Run enable plans, ceph-nfs offer is already created.
        manila.run_enable_plans(deployment, feature_config, False)

        # CreateCephNFSOfferStep calls.
        jhelper.offer_exists.assert_called_once_with("foo", microceph.NFS_OFFER_NAME)
        jhelper.create_offer.assert_not_called()

        # AddManilaDataUnitsStep calls.
        jhelper.wait_application_ready.assert_any_call(
            "manila-data",
            "foo",
            accepted_status=["active", "unknown", "blocked"],
            timeout=manila_data.MANILA_DATA_UNIT_TIMEOUT,
        )

        # Run enable plans, microceph-ceph-nfs doesn't exist.
        jhelper.offer_exists.return_value = False

        manila.run_enable_plans(deployment, feature_config, False)

        jhelper.create_offer.assert_called_once_with(
            "foo",
            microceph.APPLICATION,
            microceph.CEPH_NFS_RELATION,
            microceph.NFS_OFFER_NAME,
        )

    @patch.object(manila_feature, "JujuHelper")
    @patch.object(manila_feature, "click", Mock())
    def test_run_disable_plans(self, mock_JujuHelper, deployment):
        jhelper = mock_JujuHelper.return_value
        tfhelper = deployment.get_tfhelper.return_value
        tfhelper.state_list.return_value = []
        manila = manila_feature.SharedFilesystemFeature()
        manila._manifest = Mock()
        manila._manifest.core.software.charms = {}

        # Run disable plans, ceph-nfs offer is already created.
        manila.run_disable_plans(deployment, False)

        # DeployManilaDataApplicationStep calls.
        tfhelper.update_tfvars_and_apply_tf.assert_any_call(
            deployment.get_client.return_value,
            manila._manifest,
            tfvar_config=manila_data.CONFIG_KEY,
            override_tfvars={"machine_model": "foo"},
            tf_apply_extra_args=["-input=false", "-destroy"],
        )
        jhelper.wait_application_gone.assert_any_call(
            ["manila-data"], "foo", timeout=ANY
        )

        # DisableOpenStackApplicationStep calls.
        extra_tfvars = manila.set_tfvars_on_disable(deployment)
        extra_tfvars.update(manila.get_database_tfvars(deployment, enable=False))
        tfhelper.update_tfvars_and_apply_tf.assert_any_call(
            deployment.get_client.return_value,
            manila._manifest,
            tfvar_config="TerraformVarsOpenstack",
            override_tfvars=extra_tfvars,
        )
        jhelper.wait_application_gone.assert_any_call(
            manila.set_application_names(deployment), "openstack", timeout=ANY
        )

        # RemoveCephNFSOfferStep calls.
        jhelper.offer_exists.assert_called_once_with("foo", microceph.NFS_OFFER_NAME)
        jhelper.remove_offer.assert_called_once_with("foo", microceph.NFS_OFFER_NAME)

        # Run disable plans, microceph-ceph-nfs doesn't exist.
        jhelper.remove_offer.reset_mock()
        jhelper.offer_exists.return_value = False

        manila.run_disable_plans(deployment, False)

        jhelper.remove_offer.assert_not_called()

    def test_set_tfvars_on_enable(self, deployment):
        manila = manila_feature.SharedFilesystemFeature()
        feature_config = Mock()

        extra_tfvars = manila.set_tfvars_on_enable(deployment, feature_config)

        expected_tfvars = {
            "enable-manila": True,
            "enable-manila-cephfs": True,
            "enable-ceph-nfs": True,
            "horizon-plugins": ["foo", "manila"],
        }
        assert extra_tfvars == expected_tfvars

    def test_set_tfvars_on_disable(self, deployment):
        manila = manila_feature.SharedFilesystemFeature()

        extra_tfvars = manila.set_tfvars_on_disable(deployment)

        expected_tfvars = {
            "enable-manila": False,
            "enable-manila-cephfs": False,
            "enable-ceph-nfs": False,
            "horizon-plugins": ["foo"],
        }
        assert extra_tfvars == expected_tfvars
