# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import MagicMock, Mock, patch

from sunbeam.core.common import ResultType
from sunbeam.core.deployment import Networks
from sunbeam.core.terraform import TerraformException
from sunbeam.features.shared_filesystem.manila_data import (
    MANILA_DATA_APP_TIMEOUT,
    DeployManilaDataApplicationStep,
    DestroyManilaDataApplicationStep,
)


class TestDeployManilaDataApplicationStep(unittest.TestCase):
    def setUp(self):
        self.deployment = MagicMock()
        self.client = MagicMock()
        self.tfhelper = MagicMock()
        self.os_tfhelper = MagicMock()
        self.jhelper = MagicMock()
        self.manifest = MagicMock()
        self.model = "test-model"
        self.deployment.get_tfhelper.side_effect = lambda plan: {
            "openstack-plan": self.os_tfhelper,
        }[plan]
        self.deploy_manila_data_step = DeployManilaDataApplicationStep(
            self.deployment,
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            self.model,
        )

    def test_get_unit_timeout(self):
        self.assertEqual(
            self.deploy_manila_data_step.get_application_timeout(),
            MANILA_DATA_APP_TIMEOUT,
        )

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.read_config",
        return_value={},
    )
    def test_get_accepted_application_status(self, read_config):
        self.deploy_manila_data_step._get_offers = Mock(
            return_value={"keystone-offer-url": None}
        )

        accepted_status = self.deploy_manila_data_step.get_accepted_application_status()
        self.assertIn("blocked", accepted_status)

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.read_config",
        return_value={"keystone-offer-url": "url"},
    )
    def test_get_accepted_application_status_with_offers(self, read_config):
        self.deploy_manila_data_step._get_offers = Mock(
            return_value={"keystone-offer-url": "url"}
        )

        accepted_status = self.deploy_manila_data_step.get_accepted_application_status()
        self.assertNotIn("blocked", accepted_status)

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.get_mandatory_control_plane_offers",
        return_value={"keystone-offer-url": "url"},
    )
    def test_get_offers(self, mandatory_control_plane_offers):
        self.assertDictEqual(self.deploy_manila_data_step._offers, {})
        self.deploy_manila_data_step._get_offers()
        mandatory_control_plane_offers.assert_called_once()
        self.assertDictEqual(
            self.deploy_manila_data_step._offers,
            mandatory_control_plane_offers.return_value,
        )
        mandatory_control_plane_offers.reset_mock()
        self.deploy_manila_data_step._get_offers()
        # Should not call again
        mandatory_control_plane_offers.assert_not_called()

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.get_mandatory_control_plane_offers",
        return_value={
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        },
    )
    def test_extra_tfvars(self, get_mandatory_control_plane_offers):
        self.deployment.get_space.side_effect = lambda network: {
            Networks.MANAGEMENT: "management",
            Networks.INTERNAL: "internal",
        }[network]

        tfvars = self.deploy_manila_data_step.extra_tfvars()

        expected_tfvars = {
            "endpoint_bindings": [
                {
                    "space": "management",
                },
                {
                    "endpoint": "amqp",
                    "space": "internal",
                },
                {
                    "endpoint": "database",
                    "space": "internal",
                },
                {
                    "endpoint": "identity-credentials",
                    "space": "internal",
                },
            ],
            "charm-manila-data-config": {},
            "machine_ids": [],
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        }
        print(tfvars)
        print(expected_tfvars)
        self.assertEqual(tfvars, expected_tfvars)


class TestDestroyManilaDataApplicationStep(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.tfhelper = MagicMock()
        self.jhelper = MagicMock()
        self.manifest = MagicMock()
        self.model = "test-model"
        self.destroy_manila_data_app_step = DestroyManilaDataApplicationStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            self.model,
        )

    def test_get_unit_timeout(self):
        self.assertEqual(
            self.destroy_manila_data_app_step.get_application_timeout(),
            MANILA_DATA_APP_TIMEOUT,
        )

    def test_run_state_list_failed(self):
        self.tfhelper.state_list.side_effect = TerraformException("expected")

        result = self.destroy_manila_data_app_step.run()

        self.assertEqual(result.result_type, ResultType.FAILED)
        self.tfhelper.state_list.assert_called_once_with()

    def test_run_state_rm_failed(self):
        self.tfhelper.state_list.return_value = ["db-integration"]
        self.tfhelper.state_rm.side_effect = TerraformException("expected")

        result = self.destroy_manila_data_app_step.run()

        self.assertEqual(result.result_type, ResultType.FAILED)
        self.tfhelper.state_list.assert_called_once_with()
        self.tfhelper.state_rm.assert_called_once_with("db-integration")

    def test_run(self):
        self.tfhelper.state_list.return_value = ["db-integration", "other"]

        result = self.destroy_manila_data_app_step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        self.tfhelper.state_list.assert_called_once_with()
        self.tfhelper.state_rm.assert_called_once_with("db-integration")
