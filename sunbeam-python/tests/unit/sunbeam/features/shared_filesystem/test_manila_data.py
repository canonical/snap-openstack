# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.deployment import Networks
from sunbeam.core.terraform import TerraformException
from sunbeam.features.shared_filesystem.manila_data import (
    MANILA_DATA_APP_TIMEOUT,
    DeployManilaDataApplicationStep,
    DestroyManilaDataApplicationStep,
)


# Additional fixtures specific to manila data tests
@pytest.fixture
def basic_client():
    """Basic client mock (override to use MagicMock)."""
    return MagicMock()


@pytest.fixture
def basic_tfhelper():
    """Basic terraform helper mock (override to use MagicMock)."""
    return MagicMock()


@pytest.fixture
def basic_jhelper():
    """Basic juju helper mock (override to use MagicMock)."""
    return MagicMock()


@pytest.fixture
def basic_manifest():
    """Basic manifest mock (override to use MagicMock)."""
    return MagicMock()


@pytest.fixture
def basic_os_tfhelper():
    """Basic openstack terraform helper mock."""
    return MagicMock()


class TestDeployManilaDataApplicationStep:
    @pytest.fixture
    def deploy_manila_data_step(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_os_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
    ):
        """Deploy Manila Data step instance for testing."""
        basic_deployment.get_tfhelper.side_effect = lambda plan: {
            "openstack-plan": basic_os_tfhelper,
        }[plan]
        return DeployManilaDataApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )

    def test_get_unit_timeout(self, deploy_manila_data_step):
        assert (
            deploy_manila_data_step.get_application_timeout() == MANILA_DATA_APP_TIMEOUT
        )

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.read_config",
        return_value={},
    )
    def test_get_accepted_application_status(
        self, read_config, deploy_manila_data_step
    ):
        deploy_manila_data_step._get_offers = Mock(
            return_value={"keystone-offer-url": None}
        )

        accepted_status = deploy_manila_data_step.get_accepted_application_status()
        assert "blocked" in accepted_status

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.read_config",
        return_value={"keystone-offer-url": "url"},
    )
    def test_get_accepted_application_status_with_offers(
        self, read_config, deploy_manila_data_step
    ):
        deploy_manila_data_step._get_offers = Mock(
            return_value={"keystone-offer-url": "url"}
        )

        accepted_status = deploy_manila_data_step.get_accepted_application_status()
        assert "blocked" not in accepted_status

    @patch(
        "sunbeam.features.shared_filesystem.manila_data.get_mandatory_control_plane_offers",
        return_value={"keystone-offer-url": "url"},
    )
    def test_get_offers(self, mandatory_control_plane_offers, deploy_manila_data_step):
        assert deploy_manila_data_step._offers == {}
        deploy_manila_data_step._get_offers()
        mandatory_control_plane_offers.assert_called_once()
        assert (
            deploy_manila_data_step._offers
            == mandatory_control_plane_offers.return_value
        )
        mandatory_control_plane_offers.reset_mock()
        deploy_manila_data_step._get_offers()
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
    def test_extra_tfvars(
        self,
        get_mandatory_control_plane_offers,
        deploy_manila_data_step,
        basic_deployment,
    ):
        basic_deployment.get_space.side_effect = lambda network: {
            Networks.MANAGEMENT: "management",
            Networks.INTERNAL: "internal",
        }[network]

        tfvars = deploy_manila_data_step.extra_tfvars()

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
        assert tfvars == expected_tfvars


class TestDestroyManilaDataApplicationStep:
    @pytest.fixture
    def destroy_manila_data_step(
        self, basic_client, basic_tfhelper, basic_jhelper, basic_manifest, test_model
    ):
        """Destroy Manila Data step instance for testing."""
        return DestroyManilaDataApplicationStep(
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )

    def test_get_unit_timeout(self, destroy_manila_data_step):
        assert (
            destroy_manila_data_step.get_application_timeout()
            == MANILA_DATA_APP_TIMEOUT
        )

    def test_run_state_list_failed(self, destroy_manila_data_step, basic_tfhelper):
        basic_tfhelper.state_list.side_effect = TerraformException("expected")

        result = destroy_manila_data_step.run()

        assert result.result_type == ResultType.FAILED
        basic_tfhelper.state_list.assert_called_once_with()

    def test_run_state_rm_failed(self, destroy_manila_data_step, basic_tfhelper):
        basic_tfhelper.state_list.return_value = ["db-integration"]
        basic_tfhelper.state_rm.side_effect = TerraformException("expected")

        result = destroy_manila_data_step.run()

        assert result.result_type == ResultType.FAILED
        basic_tfhelper.state_list.assert_called_once_with()
        basic_tfhelper.state_rm.assert_called_once_with("db-integration")

    def test_run(self, destroy_manila_data_step, basic_tfhelper):
        basic_tfhelper.state_list.return_value = ["db-integration", "other"]

        result = destroy_manila_data_step.run()

        assert result.result_type == ResultType.COMPLETED
        basic_tfhelper.state_list.assert_called_once_with()
        basic_tfhelper.state_rm.assert_called_once_with("db-integration")
