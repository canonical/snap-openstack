# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import MagicMock, Mock, patch

from sunbeam.steps.cinder_volume import (
    CINDER_VOLUME_APP_TIMEOUT,
    CINDER_VOLUME_UNIT_TIMEOUT,
    DeployCinderVolumeApplicationStep,
    RemoveCinderVolumeUnitsStep,
)


class TestDeployCinderVolumeApplicationStep(unittest.TestCase):
    def setUp(self):
        self.deployment = MagicMock()
        self.client = MagicMock()
        self.tfhelper = MagicMock()
        self.os_tfhelper = MagicMock()
        self.mceph_tfhelper = MagicMock()
        self.jhelper = MagicMock()
        self.manifest = MagicMock()
        self.model = "test-model"
        self.deployment.get_tfhelper.side_effect = lambda plan: {
            "microceph-plan": self.mceph_tfhelper,
            "openstack-plan": self.os_tfhelper,
        }[plan]
        self.deploy_cinder_volume_step = DeployCinderVolumeApplicationStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            self.model,
        )

    def test_get_unit_timeout(self):
        self.assertEqual(
            self.deploy_cinder_volume_step.get_application_timeout(),
            CINDER_VOLUME_APP_TIMEOUT,
        )

    @patch(
        "sunbeam.steps.cinder_volume.get_mandatory_control_plane_offers",
        return_value={"keystone-offer-url": "url"},
    )
    def test_get_offers(self, mandatory_control_plane_offers):
        self.assertDictEqual(self.deploy_cinder_volume_step._offers, {})
        self.deploy_cinder_volume_step._get_offers()
        mandatory_control_plane_offers.assert_called_once()
        self.assertDictEqual(
            self.deploy_cinder_volume_step._offers,
            mandatory_control_plane_offers.return_value,
        )
        mandatory_control_plane_offers.reset_mock()
        self.deploy_cinder_volume_step._get_offers()
        # Should not call again
        mandatory_control_plane_offers.assert_not_called()

    def test_get_accepted_application_status(self):
        self.deploy_cinder_volume_step._get_offers = Mock(
            return_value={"keystone-offer-url": None}
        )

        accepted_status = (
            self.deploy_cinder_volume_step.get_accepted_application_status()
        )
        self.assertIn("blocked", accepted_status)

    def test_get_accepted_application_status_with_offers(self):
        self.deploy_cinder_volume_step._get_offers = Mock(
            return_value={"keystone-offer-url": "url"}
        )

        accepted_status = (
            self.deploy_cinder_volume_step.get_accepted_application_status()
        )
        self.assertNotIn("blocked", accepted_status)

    @patch("sunbeam.steps.cinder_volume.microceph.ceph_replica_scale", return_value=3)
    def test_extra_tfvars(self, mock_ceph_replica_scale):
        self.client.cluster.list_nodes_by_role.return_value = ["node1"]
        self.mceph_tfhelper.output.return_value = {"ceph-application-name": "ceph-app"}
        tfvars = self.deploy_cinder_volume_step.extra_tfvars()
        self.assertEqual(tfvars["ceph-application-name"], "ceph-app")
        self.assertEqual(
            tfvars["charm_cinder_volume_ceph_config"]["ceph-osd-replication-count"], 3
        )

    def test_extra_tfvars_after_openstack_model(self):
        self.client.cluster.list_nodes_by_role.return_value = ["node1"]
        self.os_tfhelper.output.return_value = {
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        }
        self.mceph_tfhelper.output.return_value = {"ceph-application-name": "ceph-app"}
        self.manifest.get_model.return_value = "openstack"
        tfvars = self.deploy_cinder_volume_step.extra_tfvars()
        self.assertEqual(tfvars["ceph-application-name"], "ceph-app")
        self.assertEqual(
            tfvars["charm_cinder_volume_ceph_config"]["ceph-osd-replication-count"], 1
        )

    @patch(
        "sunbeam.steps.cinder_volume.get_mandatory_control_plane_offers",
        return_value={"keystone-offer-url": "url"},
    )
    def test_extra_tfvars_no_storage_nodes(self, get_mandatory_control_plane_offers):
        self.client.cluster.list_nodes_by_role.return_value = []
        tfvars = self.deploy_cinder_volume_step.extra_tfvars()
        self.mceph_tfhelper.output.assert_not_called()
        get_mandatory_control_plane_offers.assert_not_called()
        self.assertNotIn("ceph-application-name", tfvars)
        self.assertNotIn("keystone-offer-url", tfvars)


class TestRemoveCinderVolumeUnitsStep(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.names = ["node1"]
        self.jhelper = MagicMock()
        self.model = "test-model"
        self.remove_cinder_volume_units_step = RemoveCinderVolumeUnitsStep(
            self.client,
            self.names,
            self.jhelper,
            self.model,
        )

    def test_get_unit_timeout(self):
        self.assertEqual(
            self.remove_cinder_volume_units_step.get_unit_timeout(),
            CINDER_VOLUME_UNIT_TIMEOUT,
        )
