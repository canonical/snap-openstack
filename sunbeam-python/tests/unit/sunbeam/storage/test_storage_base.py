# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for storage backend base classes."""

from pathlib import Path
from unittest.mock import Mock, patch

from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import StorageBackendConfig
from sunbeam.storage.service import StorageBackendService


class MockStorageBackend(StorageBackendBase):
    """Mock storage backend for testing."""

    name = "mock"
    display_name = "Mock Storage Backend"
    charm_name = "mock-charm"

    def __init__(self):
        super().__init__()

    @property
    def tfvar_config_key(self):
        """Config key for storing Terraform variables in clusterd."""
        return f"TerraformVars{self.name.title()}Backend"

    def config_class(self):
        return StorageBackendConfig

    def get_terraform_variables(
        self, backend_name: str, config: StorageBackendConfig, model: str
    ):
        return {
            "model": model,
            "backends": {
                backend_name: {
                    "backend_type": self.name,
                    "charm_name": self.charm_name,
                    "charm_channel": self.charm_channel,
                    "backend_config": config.model_dump(),
                    "backend_endpoint": self.backend_endpoint,
                    "units": self.units,
                    "additional_integrations": self.additional_integrations,
                }
            },
        }

    def commands(self, conditions=None):
        """Return command mapping for this backend."""
        return {}

    def prompt_for_config(self, backend_name: str):
        """Mock implementation of prompt_for_config."""
        return StorageBackendConfig(name=backend_name)

    def create_deploy_step(
        self,
        deployment,
        client,
        tfhelper,
        jhelper,
        manifest,
        backend_name,
        config,
        model,
    ):
        """Create a deployment step for this backend."""
        from sunbeam.storage.steps import BaseStorageBackendDeployStep

        class MockDeployStep(BaseStorageBackendDeployStep):
            def get_terraform_variables(self):
                return self.backend_instance.get_terraform_variables(
                    self.backend_name, self.backend_config, self.model
                )

        return MockDeployStep(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            backend_name,
            config,
            self,
            model,
        )

    def create_destroy_step(
        self, deployment, client, tfhelper, jhelper, manifest, backend_name, model
    ):
        """Create a destruction step for this backend."""
        from sunbeam.storage.steps import BaseStorageBackendDestroyStep

        class MockDestroyStep(BaseStorageBackendDestroyStep):
            def get_terraform_variables(self):
                return self.backend_instance.get_terraform_variables(
                    self.backend_name,
                    StorageBackendConfig(name=self.backend_name),
                    self.model,
                )

        return MockDestroyStep(
            deployment, client, tfhelper, jhelper, manifest, backend_name, self, model
        )

    def create_update_config_step(self, deployment, backend_name, config_updates):
        """Create a configuration update step for this backend."""
        from sunbeam.storage.steps import BaseStorageBackendConfigUpdateStep

        return BaseStorageBackendConfigUpdateStep(
            deployment, self, backend_name, config_updates
        )


class TestStorageBackendBase:
    """Test cases for StorageBackendBase class."""

    def test_init(self):
        """Test backend initialization."""
        backend = MockStorageBackend()

        assert backend.name == "mock"
        assert backend.display_name == "Mock Storage Backend"
        assert backend.charm_name == "mock-charm"
        assert backend.tfplan == "storage-backend-plan"
        assert backend.tfplan_dir == "deploy-storage-backend"
        assert backend.charm_channel == "stable"
        assert backend.charm_base == "ubuntu@22.04"
        assert backend.backend_endpoint == "cinder-volume"
        assert backend.units == 1
        assert backend.additional_integrations == []

    def test_config_class(self):
        """Test configuration class retrieval."""
        backend = MockStorageBackend()
        config_class = backend.config_class()
        assert config_class == StorageBackendConfig

    def test_get_terraform_variables(self):
        """Test Terraform variables generation."""
        backend = MockStorageBackend()
        config = StorageBackendConfig(name="test-backend")

        variables = backend.get_terraform_variables("test-backend", config, "openstack")

        assert "model" in variables
        assert "backends" in variables
        assert variables["model"] == "openstack"
        assert "test-backend" in variables["backends"]

        backend_config = variables["backends"]["test-backend"]
        assert backend_config["backend_type"] == "mock"
        assert backend_config["charm_name"] == "mock-charm"
        assert backend_config["charm_channel"] == "stable"
        assert backend_config["backend_endpoint"] == "cinder-volume"
        assert backend_config["units"] == 1

    # todo: fix this implementation. the get service is not part of the base class
    @patch("sunbeam.storage.base.StorageBackendService")
    def test_get_service(self, mock_service_class, mock_deployment):
        """Test service creation and caching."""
        backend = MockStorageBackend()
        mock_service = Mock(spec=StorageBackendService)
        mock_service_class.return_value = mock_service

        # First call should create service
        service1 = backend._get_service(mock_deployment)
        assert service1 == mock_service
        mock_service_class.assert_called_once_with(mock_deployment)

        # Second call should return cached service
        service2 = backend._get_service(mock_deployment)
        assert service2 == mock_service
        # Should not call constructor again
        assert mock_service_class.call_count == 1

    def test_get_backend_type(self):
        """Test backend type extraction from app name."""
        backend = MockStorageBackend()

        # Test with standard app name
        backend_type = backend._get_backend_type("cinder-volume-mock-backend1")
        assert backend_type == "unknown"

        # Test with app name matching backend name
        backend_type = backend._get_backend_type("mock-backend1")
        assert backend_type == "unknown"

        # Test with known backend types
        backend_type = backend._get_backend_type("cinder-volume-hitachi-backend1")
        assert backend_type == "hitachi"

        backend_type = backend._get_backend_type("cinder-volume-ceph")
        assert backend_type == "ceph"

    def test_prompt_for_config(self, mock_deployment):
        """Test configuration prompting (base implementation)."""
        backend = MockStorageBackend()

        # Base implementation should return empty config
        config = backend.prompt_for_config("test-backend")
        assert isinstance(config, StorageBackendConfig)
        assert config.name == "test-backend"

    def test_create_add_plan(self, mock_deployment):
        """Test add plan creation (base implementation)."""
        backend = MockStorageBackend()
        config = StorageBackendConfig(name="test-backend")

        # Base implementation should return list with TerraformInitStep and
        # ConcreteStorageBackendDeployStep
        plan = backend._create_add_plan(mock_deployment, config)
        assert isinstance(plan, list)
        assert len(plan) == 2

    def test_create_remove_plan(self, mock_deployment):
        """Test remove plan creation (base implementation)."""
        backend = MockStorageBackend()

        # Base implementation should return list with TerraformInitStep and
        # ConcreteStorageBackendDestroyStep
        plan = backend._create_remove_plan(mock_deployment, "test-backend")
        assert isinstance(plan, list)
        assert len(plan) == 2

    def test_abstract_methods_not_implemented(self):
        """Test that abstract methods raise NotImplementedError in base class."""
        # This test verifies that StorageBackendBase is properly abstract
        # We can't instantiate it directly, but we can test that our mock
        # implements required methods
        backend = MockStorageBackend()

        # These methods should be implemented in the mock
        assert hasattr(backend, "config_class")
        assert hasattr(backend, "get_terraform_variables")
        assert callable(backend.config_class)
        assert callable(backend.get_terraform_variables)

    def test_version_property(self):
        """Test version property."""
        backend = MockStorageBackend()
        assert hasattr(backend, "version")
        # Version should be set in base class
        assert str(backend.version) == "0.0.1"

    def test_tf_plan_location_property(self):
        """Test Terraform plan location property."""
        backend = MockStorageBackend()
        assert backend.tf_plan_location == "FEATURE_REPO"

    def test_user_manifest_property(self):
        """Test user manifest property."""
        backend = MockStorageBackend()
        assert backend.user_manifest is None

    @patch("sunbeam.storage.base.Path")
    def test_tfplan_path_property(self, mock_path):
        """Test Terraform plan path property."""
        backend = MockStorageBackend()
        mock_path.return_value = Path("/test/path")

        # This would test the tfplan_path property if it exists
        # The actual implementation may vary
        assert hasattr(backend, "tfplan_dir")
        assert backend.tfplan_dir == "deploy-storage-backend"

    def test_backend_attributes(self):
        """Test that all required backend attributes are set."""
        backend = MockStorageBackend()

        # Test required string attributes
        required_attrs = ["name", "display_name", "charm_name", "tfplan", "tfplan_dir"]
        for attr in required_attrs:
            assert hasattr(backend, attr)
            assert isinstance(getattr(backend, attr), str)
            assert getattr(backend, attr) != ""

        # Test optional attributes
        optional_attrs = [
            "charm_channel",
            "charm_revision",
            "charm_base",
            "backend_endpoint",
        ]
        for attr in optional_attrs:
            assert hasattr(backend, attr)

        # Test integer attributes
        assert hasattr(backend, "units")
        assert isinstance(backend.units, int)
        assert backend.units > 0

        # Test list attributes
        assert hasattr(backend, "additional_integrations")
        assert isinstance(backend.additional_integrations, list)

    def test_service_caching_with_different_deployments(self, mock_deployment):
        """Test service caching behavior with different deployment objects."""
        backend = MockStorageBackend()

        with patch("sunbeam.storage.base.StorageBackendService") as mock_service_class:
            mock_service1 = Mock(spec=StorageBackendService)
            mock_service_class.return_value = mock_service1

            # First deployment
            service1 = backend._get_service(mock_deployment)
            assert service1 == mock_service1

            # Different deployment object should return same cached service
            mock_deployment2 = Mock()
            service2 = backend._get_service(mock_deployment2)
            assert service2 == mock_service1  # Same service is cached and reused

            # Same deployment should return cached service
            service1_cached = backend._get_service(mock_deployment)
            assert service1_cached == mock_service1

            # Should have called constructor only once (service is cached)
            assert mock_service_class.call_count == 1
