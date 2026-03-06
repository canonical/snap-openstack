# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.features.telemetry import feature as telemetry_feature


@pytest.fixture()
def deployment():
    deploy = Mock()
    deploy.openstack_machines_model = "openstack"
    deploy.juju_controller = "test-controller"

    client = deploy.get_client.return_value
    client.cluster.list_nodes_by_role.return_value = [{"name": "node1", "machineid": 1}]

    return deploy


@pytest.fixture()
def mock_storage_backends():
    """Mock storage backends with different principal applications."""
    backend1 = Mock()
    backend1.name = "backend1"
    backend1.type = "type1"
    backend1.principal = "cinder-volume-noha"

    backend2 = Mock()
    backend2.name = "backend2"
    backend2.type = "type2"
    backend2.principal = "cinder-volume-noha"  # Same principal as backend1

    backend3 = Mock()
    backend3.name = "backend3"
    backend3.type = "type3"
    backend3.principal = "cinder-volume"  # Different principal

    return [backend1, backend2, backend3]


@pytest.fixture()
def mock_backend_instances():
    """Mock backend instances from StorageBackendManager."""
    instance1 = Mock()
    instance1.principal_application = "cinder-volume-noha"

    instance2 = Mock()
    instance2.principal_application = "cinder-volume-noha"

    instance3 = Mock()
    instance3.principal_application = "cinder-volume"

    return {
        "type1": instance1,
        "type2": instance2,
        "type3": instance3,
    }


class TestTelemetryFeatureDeduplication:
    """Test deduplication logic in telemetry feature enable/disable plans."""

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.DeploySpecificCinderVolumeStep")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_enable_plans_deduplicates_shared_principals(
        self,
        mock_run_plan,
        mock_deploy_step_class,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
        mock_storage_backends,
        mock_backend_instances,
    ):
        """Test that enable plans deduplicates backends sharing the same principal."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = mock_storage_backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock StorageBackendManager
        mock_storage_manager = mock_storage_manager_class.return_value
        mock_storage_manager.backends.return_value = mock_backend_instances

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {"ceilometer-offer-url": "url"}
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
            "storage-plan": tfhelper_storage,
        }[plan]

        # Create feature and run enable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        # Verify DeploySpecificCinderVolumeStep was called only twice
        # (once for cinder-volume-noha, once for cinder-volume)
        # NOT three times (which would be without deduplication)
        assert mock_deploy_step_class.call_count == 2

        # Verify the principals that were processed
        principals_processed = set()
        for call in mock_deploy_step_class.call_args_list:
            backend_instance = call[0][6]  # 7th positional arg is backend_instance
            principals_processed.add(backend_instance.principal_application)

        assert principals_processed == {"cinder-volume-noha", "cinder-volume"}

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.DeploySpecificCinderVolumeStep")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_disable_plans_deduplicates_shared_principals(
        self,
        mock_run_plan,
        mock_deploy_step_class,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
        mock_storage_backends,
        mock_backend_instances,
    ):
        """Test that disable plans deduplicates backends sharing the same principal."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = mock_storage_backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock StorageBackendManager
        mock_storage_manager = mock_storage_manager_class.return_value
        mock_storage_manager.backends.return_value = mock_backend_instances

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper.state_list.return_value = []
        tfhelper_openstack = Mock()
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
            "storage-plan": tfhelper_storage,
        }[plan]

        # Create feature and run disable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, False)

        # Verify DeploySpecificCinderVolumeStep was called only twice
        # (once for cinder-volume-noha, once for cinder-volume)
        assert mock_deploy_step_class.call_count == 2

        # Verify the principals that were processed
        principals_processed = set()
        for call in mock_deploy_step_class.call_args_list:
            backend_instance = call[0][6]  # 7th positional arg is backend_instance
            principals_processed.add(backend_instance.principal_application)

        assert principals_processed == {"cinder-volume-noha", "cinder-volume"}

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_enable_plans_no_storage_backends(
        self,
        mock_run_plan,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
    ):
        """Test that enable plans works when there are no storage backends."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = []  # No backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {"ceilometer-offer-url": "url"}
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
        }[plan]

        # Create feature and run enable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        # Verify run_plan was called for plan1 and plan2, but not plan3
        # (plan3 is for storage backends which we don't have)
        assert mock_run_plan.call_count == 2

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.DeploySpecificCinderVolumeStep")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_enable_plans_passes_extra_tfvars(
        self,
        mock_run_plan,
        mock_deploy_step_class,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
        mock_storage_backends,
        mock_backend_instances,
    ):
        """Test that enable plans passes correct extra_tfvars to steps."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = mock_storage_backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock StorageBackendManager
        mock_storage_manager = mock_storage_manager_class.return_value
        mock_storage_manager.backends.return_value = mock_backend_instances

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper_openstack = Mock()
        tfhelper_openstack.output.return_value = {"ceilometer-offer-url": "url"}
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
            "storage-plan": tfhelper_storage,
        }[plan]

        # Create feature and run enable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_enable_plans(deployment, Mock(), False)

        # Verify all DeploySpecificCinderVolumeStep calls have correct extra_tfvars
        for call in mock_deploy_step_class.call_args_list:
            extra_tfvars = call[1]["extra_tfvars"]
            assert extra_tfvars == {"enable-telemetry-notifications": True}

    @patch("sunbeam.features.telemetry.feature.JujuHelper")
    @patch("sunbeam.features.telemetry.feature.StorageBackendManager")
    @patch("sunbeam.features.telemetry.feature.DeploySpecificCinderVolumeStep")
    @patch("sunbeam.features.telemetry.feature.run_plan")
    def test_run_disable_plans_passes_extra_tfvars(
        self,
        mock_run_plan,
        mock_deploy_step_class,
        mock_storage_manager_class,
        mock_jhelper_class,
        deployment,
        mock_storage_backends,
        mock_backend_instances,
    ):
        """Test that disable plans passes correct extra_tfvars to steps."""
        # Setup mocks
        client = deployment.get_client.return_value
        storage_backends_root = Mock()
        storage_backends_root.root = mock_storage_backends
        client.cluster.get_storage_backends.return_value = storage_backends_root

        # Mock StorageBackendManager
        mock_storage_manager = mock_storage_manager_class.return_value
        mock_storage_manager.backends.return_value = mock_backend_instances

        # Mock tfhelpers
        tfhelper = Mock()
        tfhelper.state_list.return_value = []
        tfhelper_openstack = Mock()
        tfhelper_hypervisor = Mock()
        tfhelper_cinder_volume = Mock()
        tfhelper_storage = Mock()

        deployment.get_tfhelper.side_effect = lambda plan: {
            "telemetry-plan": tfhelper,
            "openstack-plan": tfhelper_openstack,
            "hypervisor-plan": tfhelper_hypervisor,
            "cinder-volume-plan": tfhelper_cinder_volume,
            "storage-plan": tfhelper_storage,
        }[plan]

        # Create feature and run disable plans
        feature = telemetry_feature.TelemetryFeature()
        feature._manifest = Mock()
        feature.run_disable_plans(deployment, False)

        # Verify all DeploySpecificCinderVolumeStep calls have correct extra_tfvars
        for call in mock_deploy_step_class.call_args_list:
            extra_tfvars = call[1]["extra_tfvars"]
            assert extra_tfvars == {"enable-telemetry-notifications": False}
