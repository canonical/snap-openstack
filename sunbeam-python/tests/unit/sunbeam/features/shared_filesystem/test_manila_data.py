# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import MagicMock, Mock, patch

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.core.common import ResultType
from sunbeam.core.deployment import Networks
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.core.terraform import TerraformException
from sunbeam.features.shared_filesystem.manila_data import (
    APPLICATION,
    MANILA_DATA_APP_TIMEOUT,
    MANILA_DATA_UNIT_TIMEOUT,
    AddManilaDataUnitsStep,
    CheckManilaDataDistributionStep,
    DeployManilaDataApplicationStep,
    DestroyManilaDataApplicationStep,
    RemoveManilaDataUnitsStep,
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

    def test_get_accepted_application_status(self):
        self.deploy_manila_data_step._get_offers = Mock(
            return_value={"keystone-offer-url": None}
        )

        accepted_status = self.deploy_manila_data_step.get_accepted_application_status()
        self.assertIn("blocked", accepted_status)

    def test_get_accepted_application_status_with_offers(self):
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
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        }
        self.assertEqual(tfvars, expected_tfvars)


class TestAddManilaDataUnitsStep(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.names = ["node1"]
        self.jhelper = MagicMock()
        self.model = "test-model"
        self.os_tfhelper = MagicMock()
        self.add_manila_data_units_step = AddManilaDataUnitsStep(
            self.client,
            self.names,
            self.jhelper,
            self.model,
            self.os_tfhelper,
        )

    def test_get_unit_timeout(self):
        self.assertEqual(
            self.add_manila_data_units_step.get_unit_timeout(),
            MANILA_DATA_UNIT_TIMEOUT,
        )

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.get_mandatory_control_plane_offers",
        return_value={
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        },
    )
    @patch(
        "sunbeam.features.shared_filesystem.manila_data.read_config",
        return_value={
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        },
    )
    def test_get_accepted_unit_status(
        self, get_mandatory_control_plane_offers, read_config
    ):
        self.client.cluster.list_nodes_by_role.return_value = ["node1"]
        accepted_status = self.add_manila_data_units_step.get_accepted_unit_status()
        self.assertNotIn("blocked", accepted_status["workload"])
        get_mandatory_control_plane_offers.assert_called_once()
        read_config.assert_called_once()

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.get_mandatory_control_plane_offers",
        return_value={"keystone-offer-url": None},
    )
    def test_get_accepted_unit_status_with_missing_offers(
        self, get_mandatory_control_plane_offers
    ):
        accepted_status = self.add_manila_data_units_step.get_accepted_unit_status()
        self.assertIn("blocked", accepted_status["workload"])
        get_mandatory_control_plane_offers.assert_called_once()

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.get_mandatory_control_plane_offers",
        return_value={
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        },
    )
    @patch(
        "sunbeam.features.shared_filesystem.manila_data.read_config",
        return_value={
            "keystone-offer-url": "keystone-differ",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        },
    )
    def test_get_accepted_unit_status_with_different_config(
        self, get_mandatory_control_plane_offers, read_config
    ):
        accepted_status = self.add_manila_data_units_step.get_accepted_unit_status()
        self.assertIn("blocked", accepted_status["workload"])
        get_mandatory_control_plane_offers.assert_called_once()
        read_config.assert_called_once()

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.get_mandatory_control_plane_offers",
        return_value={
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        },
    )
    @patch(
        "sunbeam.features.shared_filesystem.manila_data.read_config",
        side_effect=ConfigItemNotFoundException("config not found"),
    )
    def test_get_accepted_unit_status_with_config_exception(
        self, get_mandatory_control_plane_offers, read_config
    ):
        accepted_status = self.add_manila_data_units_step.get_accepted_unit_status()
        self.assertIn("blocked", accepted_status["workload"])
        get_mandatory_control_plane_offers.assert_called_once()
        read_config.assert_called_once()


class TestRemoveManilaDataUnitsStep(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.names = ["node1"]
        self.jhelper = MagicMock()
        self.model = "test-model"
        self.remove_manila_data_units_step = RemoveManilaDataUnitsStep(
            self.client,
            self.names,
            self.jhelper,
            self.model,
        )

    def test_get_unit_timeout(self):
        self.assertEqual(
            self.remove_manila_data_units_step.get_unit_timeout(),
            MANILA_DATA_UNIT_TIMEOUT,
        )


class TestCheckManilaDataDistributionStep(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.name = "node1"
        self.jhelper = MagicMock()
        self.model = "test-model"
        self.check_manila_data_dist_step = CheckManilaDataDistributionStep(
            self.client,
            self.name,
            self.jhelper,
            self.model,
        )

    def test_is_skip_no_node(self):
        self.client.cluster.get_node_info.side_effect = NodeNotExistInClusterException()

        result = self.check_manila_data_dist_step.is_skip()

        self.assertEqual(result.result_type, ResultType.FAILED)
        self.client.cluster.get_node_info.assert_called_once_with(self.name)

    def test_is_skip_no_storage_role(self):
        self.client.cluster.get_node_info.return_value = {}

        result = self.check_manila_data_dist_step.is_skip()

        self.assertEqual(result.result_type, ResultType.SKIPPED)
        self.client.cluster.get_node_info.assert_called_once_with(self.name)

    def test_is_skip_no_app(self):
        self.client.cluster.get_node_info.return_value = {"role": "storage"}
        self.jhelper.get_application.side_effect = ApplicationNotFoundException()

        result = self.check_manila_data_dist_step.is_skip()

        self.assertEqual(result.result_type, ResultType.SKIPPED)
        self.client.cluster.get_node_info.assert_called_once_with(self.name)
        self.jhelper.get_application.assert_called_once_with(APPLICATION, self.model)

    def test_is_skip_no_units(self):
        self.client.cluster.get_node_info.return_value = {
            "role": "storage",
            "machineid": "foo",
        }

        mock_unit = Mock()
        mock_unit.machine = "lish"
        mock_app = self.jhelper.get_application.return_value
        mock_app.units = {"foo/0": mock_unit}

        result = self.check_manila_data_dist_step.is_skip()

        self.assertEqual(result.result_type, ResultType.SKIPPED)
        self.client.cluster.get_node_info.assert_called_once_with(self.name)
        self.jhelper.get_application.assert_called_once_with(APPLICATION, self.model)

    def test_is_skip(self):
        self.client.cluster.get_node_info.return_value = {
            "role": "storage",
            "machineid": "foo",
        }

        mock_unit = Mock()
        mock_unit.machine = "foo"
        mock_app = self.jhelper.get_application.return_value
        mock_app.units = {"foo/0": mock_unit}

        result = self.check_manila_data_dist_step.is_skip()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        self.client.cluster.get_node_info.assert_called_once_with(self.name)
        self.jhelper.get_application.assert_called_once_with(APPLICATION, self.model)


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
