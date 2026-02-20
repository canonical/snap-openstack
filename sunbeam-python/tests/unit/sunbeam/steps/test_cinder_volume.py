# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, Mock, patch

import pytest

from sunbeam.steps.cinder_volume import (
    CINDER_VOLUME_APP_TIMEOUT,
    CINDER_VOLUME_UNIT_TIMEOUT,
    DeployCinderVolumeApplicationStep,
    RemoveCinderVolumeUnitsStep,
)


# Common fixtures
# Additional fixtures specific to cinder volume tests
@pytest.fixture
def os_tfhelper():
    """OpenStack tfhelper mock."""
    return MagicMock()


@pytest.fixture
def mceph_tfhelper():
    """MicroCeph tfhelper mock."""
    return MagicMock()


@pytest.fixture
def deployment_with_tfhelpers(basic_deployment, os_tfhelper, mceph_tfhelper):
    """Deployment mock with configured tfhelpers."""
    basic_deployment.get_tfhelper.side_effect = lambda plan: {
        "microceph-plan": mceph_tfhelper,
        "openstack-plan": os_tfhelper,
    }[plan]
    return basic_deployment


class TestDeployCinderVolumeApplicationStep:
    @pytest.fixture
    def deploy_cinder_volume_step(
        self,
        deployment_with_tfhelpers,
        basic_client,
        tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
    ):
        """Create DeployCinderVolumeApplicationStep instance for testing."""
        return DeployCinderVolumeApplicationStep(
            deployment_with_tfhelpers,
            basic_client,
            tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )

    def test_get_unit_timeout(self, deploy_cinder_volume_step):
        assert (
            deploy_cinder_volume_step.get_application_timeout()
            == CINDER_VOLUME_APP_TIMEOUT
        )

    @patch(
        "sunbeam.steps.cinder_volume.get_mandatory_control_plane_offers",
        return_value={"keystone-offer-url": "url"},
    )
    def test_get_offers(
        self, mandatory_control_plane_offers, deploy_cinder_volume_step
    ):
        assert deploy_cinder_volume_step._offers == {}
        deploy_cinder_volume_step._get_offers()
        mandatory_control_plane_offers.assert_called_once()
        assert (
            deploy_cinder_volume_step._offers
            == mandatory_control_plane_offers.return_value
        )
        mandatory_control_plane_offers.reset_mock()
        deploy_cinder_volume_step._get_offers()
        # Should not call again
        mandatory_control_plane_offers.assert_not_called()

    def test_get_accepted_application_status(self, deploy_cinder_volume_step):
        deploy_cinder_volume_step._get_offers = Mock(
            return_value={"keystone-offer-url": None}
        )

        accepted_status = deploy_cinder_volume_step.get_accepted_application_status()
        assert "blocked" in accepted_status

    def test_get_accepted_application_status_with_offers(
        self, deploy_cinder_volume_step
    ):
        deploy_cinder_volume_step._get_offers = Mock(
            return_value={"keystone-offer-url": "url"}
        )

        accepted_status = deploy_cinder_volume_step.get_accepted_application_status()
        assert "blocked" not in accepted_status

    @patch("sunbeam.steps.cinder_volume.microceph.ceph_replica_scale", return_value=3)
    def test_extra_tfvars(
        self,
        mock_ceph_replica_scale,
        deploy_cinder_volume_step,
        basic_client,
        mceph_tfhelper,
    ):
        basic_client.cluster.list_nodes_by_role.return_value = ["node1"]
        mceph_tfhelper.output.return_value = {"ceph-application-name": "ceph-app"}
        tfvars = deploy_cinder_volume_step.extra_tfvars()
        assert tfvars["ceph-application-name"] == "ceph-app"
        assert (
            tfvars["charm_cinder_volume_ceph_config"]["ceph-osd-replication-count"] == 3
        )

    def test_extra_tfvars_after_openstack_model(
        self,
        deploy_cinder_volume_step,
        basic_client,
        os_tfhelper,
        mceph_tfhelper,
        basic_manifest,
    ):
        basic_client.cluster.list_nodes_by_role.return_value = ["node1"]
        os_tfhelper.output.return_value = {
            "keystone-offer-url": "keystone-offer",
            "database-offer-url": "database-offer",
            "amqp-offer-url": "amqp-offer",
        }
        mceph_tfhelper.output.return_value = {"ceph-application-name": "ceph-app"}
        basic_manifest.get_model.return_value = "openstack"
        tfvars = deploy_cinder_volume_step.extra_tfvars()
        assert tfvars["ceph-application-name"] == "ceph-app"
        assert (
            tfvars["charm_cinder_volume_ceph_config"]["ceph-osd-replication-count"] == 1
        )

    @patch(
        "sunbeam.steps.cinder_volume.get_mandatory_control_plane_offers",
        return_value={"keystone-offer-url": "url"},
    )
    def test_extra_tfvars_no_storage_nodes(
        self,
        get_mandatory_control_plane_offers,
        deploy_cinder_volume_step,
        basic_client,
        mceph_tfhelper,
    ):
        basic_client.cluster.list_nodes_by_role.return_value = []
        tfvars = deploy_cinder_volume_step.extra_tfvars()
        mceph_tfhelper.output.assert_not_called()
        get_mandatory_control_plane_offers.assert_not_called()
        assert "ceph-application-name" not in tfvars
        assert "keystone-offer-url" not in tfvars

    def test_init_with_extra_tfvars(
        self,
        deployment_with_tfhelpers,
        basic_client,
        tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
    ):
        """Test that extra_tfvars parameter is stored as override_tfvars."""
        extra_tfvars = {"enable-telemetry-notifications": True, "custom-key": "value"}
        step = DeployCinderVolumeApplicationStep(
            deployment_with_tfhelpers,
            basic_client,
            tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
            extra_tfvars=extra_tfvars,
        )
        assert step.override_tfvars == extra_tfvars

    def test_init_without_extra_tfvars(
        self,
        deployment_with_tfhelpers,
        basic_client,
        tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
    ):
        """Test that override_tfvars defaults to empty dict.

        When extra_tfvars is not provided.
        """
        step = DeployCinderVolumeApplicationStep(
            deployment_with_tfhelpers,
            basic_client,
            tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )
        assert step.override_tfvars == {}

    @patch("sunbeam.steps.cinder_volume.microceph.ceph_replica_scale", return_value=3)
    def test_extra_tfvars_override_precedence(
        self,
        mock_ceph_replica_scale,
        deployment_with_tfhelpers,
        basic_client,
        tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        mceph_tfhelper,
    ):
        """Test that override_tfvars values take precedence over computed tfvars."""
        basic_client.cluster.list_nodes_by_role.return_value = ["node1"]
        mceph_tfhelper.output.return_value = {"ceph-application-name": "ceph-app"}

        # Create step with override_tfvars
        override_tfvars = {
            "enable-telemetry-notifications": True,
            "ceph-application-name": "override-ceph-app",
        }
        step = DeployCinderVolumeApplicationStep(
            deployment_with_tfhelpers,
            basic_client,
            tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
            extra_tfvars=override_tfvars,
        )

        # Mock the feature manager to return disabled telemetry
        feature_manager = Mock()
        feature_manager.is_feature_enabled.return_value = False
        deployment_with_tfhelpers.get_feature_manager.return_value = feature_manager

        tfvars = step.extra_tfvars()

        # Verify override_tfvars values take precedence
        assert tfvars["enable-telemetry-notifications"] is True  # overridden
        assert tfvars["ceph-application-name"] == "override-ceph-app"  # overridden

    @patch("sunbeam.steps.cinder_volume.microceph.ceph_replica_scale", return_value=3)
    def test_extra_tfvars_telemetry_feature_enabled(
        self,
        mock_ceph_replica_scale,
        deployment_with_tfhelpers,
        basic_client,
        tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        mceph_tfhelper,
    ):
        """Test telemetry notifications are enabled.

        When telemetry feature is enabled.
        """
        basic_client.cluster.list_nodes_by_role.return_value = []

        step = DeployCinderVolumeApplicationStep(
            deployment_with_tfhelpers,
            basic_client,
            tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )

        # Mock the feature manager to return enabled telemetry
        feature_manager = Mock()
        feature_manager.is_feature_enabled.return_value = True
        deployment_with_tfhelpers.get_feature_manager.return_value = feature_manager

        tfvars = step.extra_tfvars()

        # Verify telemetry notifications are enabled
        assert tfvars["enable-telemetry-notifications"] is True
        feature_manager.is_feature_enabled.assert_called_once_with(
            deployment_with_tfhelpers, "telemetry"
        )

    @patch("sunbeam.steps.cinder_volume.microceph.ceph_replica_scale", return_value=3)
    def test_extra_tfvars_telemetry_feature_disabled(
        self,
        mock_ceph_replica_scale,
        deployment_with_tfhelpers,
        basic_client,
        tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        mceph_tfhelper,
    ):
        """Test telemetry notifications are disabled.

        When telemetry feature is disabled.
        """
        basic_client.cluster.list_nodes_by_role.return_value = []

        step = DeployCinderVolumeApplicationStep(
            deployment_with_tfhelpers,
            basic_client,
            tfhelper,
            basic_jhelper,
            basic_manifest,
            test_model,
        )

        # Mock the feature manager to return disabled telemetry
        feature_manager = Mock()
        feature_manager.is_feature_enabled.return_value = False
        deployment_with_tfhelpers.get_feature_manager.return_value = feature_manager

        tfvars = step.extra_tfvars()

        # Verify telemetry notifications are disabled
        assert tfvars["enable-telemetry-notifications"] is False
        feature_manager.is_feature_enabled.assert_called_once_with(
            deployment_with_tfhelpers, "telemetry"
        )


class TestRemoveCinderVolumeUnitsStep:
    @pytest.fixture
    def test_names(self):
        """Test node names."""
        return ["node1"]

    @pytest.fixture
    def remove_cinder_volume_units_step(
        self, basic_client, test_names, basic_jhelper, test_model
    ):
        """Create RemoveCinderVolumeUnitsStep instance for testing."""
        return RemoveCinderVolumeUnitsStep(
            basic_client,
            test_names,
            basic_jhelper,
            test_model,
        )

    def test_get_unit_timeout(self, remove_cinder_volume_units_step):
        assert (
            remove_cinder_volume_units_step.get_unit_timeout()
            == CINDER_VOLUME_UNIT_TIMEOUT
        )
