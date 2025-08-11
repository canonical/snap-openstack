# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Terraform-based storage backend step classes."""

from unittest.mock import Mock, patch

from sunbeam.core.common import ResultType
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import StorageBackendConfig
from sunbeam.storage.steps import (
    BaseStorageBackendConfigUpdateStep,
    BaseStorageBackendDeployStep,
    BaseStorageBackendDestroyStep,
)


class MockStorageBackend(StorageBackendBase):
    """Mock storage backend for testing."""

    name = "mock"
    display_name = "Mock Storage Backend"
    charm_name = "mock-charm"
    tfplan = "mock-backend-plan"
    config_class = StorageBackendConfig

    @property
    def tfvar_config_key(self):
        """Config key for storing Terraform variables in clusterd."""
        return f"TerraformVars{self.name.title()}Backend"

    def register_add_cli(self, add):
        """Mock CLI registration."""
        pass

    def register_cli(
        self, remove, config_show, config_set, config_reset, config_options, deployment
    ):
        """Mock CLI registration."""
        pass

    def get_terraform_variables(self, backend_name, config, model):
        """Mock Terraform variables."""
        return {
            "model": model,
            "mock_backends": {
                backend_name: {
                    "backend_type": self.name,
                    "charm_name": self.charm_name,
                    "charm_channel": "latest/stable",
                    "backend_config": config.model_dump(),
                    "backend_endpoint": "storage-backend",
                    "units": 1,
                    "additional_integrations": {},
                }
            },
        }

    def create_deploy_step(
        self,
        deployment,
        client,
        tfhelper,
        jhelper,
        manifest,
        backend_name,
        backend_config,
        model,
    ):
        """Create a mock deployment step."""
        return MockDeployStep(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            backend_name,
            backend_config,
            self,
            model,
        )

    def create_destroy_step(
        self, deployment, client, tfhelper, jhelper, manifest, backend_name, model
    ):
        """Create a mock destruction step."""
        return MockDestroyStep(
            deployment, client, tfhelper, jhelper, manifest, backend_name, self, model
        )

    def create_update_config_step(self, deployment, backend_name, config_updates):
        """Create a mock configuration update step."""
        return BaseStorageBackendConfigUpdateStep(
            deployment, self, backend_name, config_updates
        )

    def prompt_for_config(self, backend_name: str) -> StorageBackendConfig:
        """Mock prompt for configuration."""
        return StorageBackendConfig(name=backend_name)

    def get_field_mapping(self):
        """Return field mapping for mock backend."""
        return {}

    def commands(self):
        """Return commands for mock backend."""
        return []


class MockDeployStep(BaseStorageBackendDeployStep):
    """Concrete implementation of BaseStorageBackendDeployStep for testing."""

    def get_terraform_variables(self) -> dict:
        """Get terraform variables for deployment."""
        return self.backend_instance.get_terraform_variables(
            self.backend_name, self.backend_config, self.model
        )


class MockDestroyStep(BaseStorageBackendDestroyStep):
    """Concrete implementation of BaseStorageBackendDestroyStep for testing."""

    pass


class TestBaseStorageBackendDeployStep:
    """Test cases for BaseStorageBackendDeployStep."""

    def test_init(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test step initialization."""
        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        backend_config = StorageBackendConfig(name=backend_name)
        model = "openstack"

        step = MockDeployStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_config,
            backend_instance,
            model,
        )

        assert step.deployment == mock_deployment
        assert step.client == mock_client
        assert step.tfhelper == mock_tfhelper
        assert step.jhelper == mock_jhelper
        assert step.manifest == mock_manifest
        assert step.backend_name == backend_name
        assert step.backend_config == backend_config
        assert step.backend_instance == backend_instance
        assert step.model == model
        assert "Deploy Mock Storage Backend" in step.name
        assert "test-backend" in step.description

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.update_config")
    def test_run_success(
        self,
        mock_update_config,
        mock_read_config,
        mock_deployment,
        mock_client,
        mock_tfhelper,
        mock_jhelper,
        mock_manifest,
    ):
        """Test successful deployment run."""
        # Mock existing config
        mock_read_config.return_value = {"existing": "config"}

        # Mock Terraform operations
        mock_tfhelper.update_tfvars_and_apply_tf.return_value = None

        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        backend_config = StorageBackendConfig(name=backend_name)
        model = "openstack"

        step = MockDeployStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_config,
            backend_instance,
            model,
        )

        result = step.run()

        assert result.result_type == ResultType.COMPLETED

        # Verify Terraform was called with correct variables
        mock_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        call_args = mock_tfhelper.update_tfvars_and_apply_tf.call_args
        tfvars = call_args[1]["override_tfvars"]

        assert "model" in tfvars
        assert "mock_backends" in tfvars
        assert tfvars["model"] == "openstack"

    def test_get_application_timeout(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test application timeout retrieval."""
        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        backend_config = StorageBackendConfig(name=backend_name)
        model = "openstack"

        step = MockDeployStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_config,
            backend_instance,
            model,
        )

        timeout = step.get_application_timeout()
        assert timeout == 1200  # Default timeout


class TestBaseStorageBackendDestroyStep:
    """Test cases for BaseStorageBackendDestroyStep."""

    def test_init(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test step initialization."""
        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        model = "openstack"

        step = BaseStorageBackendDestroyStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_instance,
            model,
        )

        assert step.deployment == mock_deployment
        assert step.client == mock_client
        assert step.tfhelper == mock_tfhelper
        assert step.jhelper == mock_jhelper
        assert step.manifest == mock_manifest
        assert step.backend_name == backend_name
        assert step.backend_instance == backend_instance
        assert step.model == model
        assert "Destroy Mock Storage Backend" in step.name
        assert "test-backend" in step.description

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.update_config")
    def test_run_success_partial_destroy(
        self,
        mock_update_config,
        mock_read_config,
        mock_deployment,
        mock_client,
        mock_tfhelper,
        mock_jhelper,
        mock_manifest,
    ):
        """Test successful destruction run with partial destroy."""
        # Mock existing config with multiple backends
        mock_read_config.return_value = {
            "mock_backends": {
                "test-backend": {"backend_type": "mock"},
                "other-backend": {"backend_type": "hitachi"},
            }
        }

        # Mock Terraform operations
        mock_tfhelper.apply.return_value = None

        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        model = "openstack"

        step = BaseStorageBackendDestroyStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_instance,
            model,
        )

        # Mock should_destroy_all_resources to return False
        with patch.object(step, "should_destroy_all_resources", return_value=False):
            result = step.run()

        assert result.result_type == ResultType.COMPLETED

        # Verify Terraform was called
        mock_tfhelper.apply.assert_called_once()

    @patch("sunbeam.storage.steps.read_config")
    def test_run_success_full_destroy(
        self,
        mock_read_config,
        mock_deployment,
        mock_client,
        mock_tfhelper,
        mock_jhelper,
        mock_manifest,
    ):
        """Test successful destruction run with full destroy."""
        # Mock existing config with only one backend
        mock_read_config.return_value = {
            "mock_backends": {"test-backend": {"backend_type": "mock"}}
        }

        # Mock Terraform operations
        mock_tfhelper.destroy.return_value = None

        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        model = "openstack"

        step = BaseStorageBackendDestroyStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_instance,
            model,
        )

        # Mock should_destroy_all_resources to return True
        with patch.object(step, "should_destroy_all_resources", return_value=True):
            result = step.run()

        assert result.result_type == ResultType.COMPLETED

        # Verify Terraform destroy was called
        mock_tfhelper.destroy.assert_called_once()

    @patch("sunbeam.storage.steps.read_config")
    def test_should_destroy_all_resources_true(
        self,
        mock_read_config,
        mock_deployment,
        mock_client,
        mock_tfhelper,
        mock_jhelper,
        mock_manifest,
    ):
        """Test should_destroy_all_resources logic when only one backend exists."""
        # Mock config with only one backend
        mock_read_config.return_value = {
            "mock_backends": {"test-backend": {"backend_type": "mock"}}
        }

        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        model = "openstack"

        step = BaseStorageBackendDestroyStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_instance,
            model,
        )

        result = step.should_destroy_all_resources()
        assert result is True

    @patch("sunbeam.storage.steps.read_config")
    def test_should_destroy_all_resources_false(
        self,
        mock_read_config,
        mock_deployment,
        mock_client,
        mock_tfhelper,
        mock_jhelper,
        mock_manifest,
    ):
        """Test should_destroy_all_resources logic when multiple backends exist."""
        # Mock config with multiple backends
        mock_read_config.return_value = {
            "mock_backends": {
                "test-backend": {"backend_type": "mock"},
                "other-backend": {"backend_type": "hitachi"},
            }
        }

        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        model = "openstack"

        step = BaseStorageBackendDestroyStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_instance,
            model,
        )

        result = step.should_destroy_all_resources()
        assert result is False

    def test_get_application_timeout(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test application timeout retrieval."""
        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        model = "openstack"

        step = BaseStorageBackendDestroyStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_instance,
            model,
        )

        timeout = step.get_application_timeout()
        assert timeout == 1200  # Default timeout


class TestBaseStorageBackendConfigUpdateStep:
    """Test cases for BaseStorageBackendConfigUpdateStep."""

    def test_init(self, mock_deployment):
        """Test step initialization."""
        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        config_updates = {"key1": "value1", "key2": "value2"}

        # Mock deployment methods
        mock_client = Mock()
        mock_tfhelper = Mock()
        mock_deployment.get_client.return_value = mock_client
        mock_deployment.get_tfhelper.return_value = mock_tfhelper

        step = BaseStorageBackendConfigUpdateStep(
            mock_deployment, backend_instance, backend_name, config_updates
        )

        assert step.deployment == mock_deployment
        assert step.backend_instance == backend_instance
        assert step.backend_name == backend_name
        assert step.config_updates == config_updates
        assert step.client == mock_client
        assert step.tfhelper == mock_tfhelper
        assert "Update Mock Storage Backend" in step.name
        assert "test-backend" in step.description

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.update_config")
    def test_run_update_operation(
        self, mock_update_config, mock_read_config, mock_deployment
    ):
        """Test configuration update operation."""
        # Mock existing config
        mock_read_config.return_value = {
            "mock_backends": {
                "test-backend": {"backend_config": {"existing_key": "existing_value"}}
            }
        }

        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        config_updates = {"key1": "value1", "key2": "value2"}

        # Mock deployment methods
        mock_client = Mock()
        mock_tfhelper = Mock()
        mock_tfhelper.write_tfvars.return_value = None
        mock_tfhelper.apply.return_value = None
        mock_deployment.get_client.return_value = mock_client
        mock_deployment.get_tfhelper.return_value = mock_tfhelper

        step = BaseStorageBackendConfigUpdateStep(
            mock_deployment, backend_instance, backend_name, config_updates
        )

        result = step.run()

        assert result.result_type == ResultType.COMPLETED

        # Verify Terraform was called
        mock_tfhelper.apply.assert_called_once()

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.update_config")
    def test_run_reset_operation(
        self, mock_update_config, mock_read_config, mock_deployment
    ):
        """Test configuration reset operation."""
        # Mock existing config
        mock_read_config.return_value = {
            "mock_backends": {
                "test-backend": {"backend_config": {"key1": "value1", "key2": "value2"}}
            }
        }

        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        config_updates = {"_reset_keys": ["key1"]}

        # Mock deployment methods
        mock_client = Mock()
        mock_tfhelper = Mock()
        mock_tfhelper.write_tfvars.return_value = None
        mock_tfhelper.apply.return_value = None
        mock_deployment.get_client.return_value = mock_client
        mock_deployment.get_tfhelper.return_value = mock_tfhelper

        step = BaseStorageBackendConfigUpdateStep(
            mock_deployment, backend_instance, backend_name, config_updates
        )

        result = step.run()

        assert result.result_type == ResultType.COMPLETED

        # Verify Terraform was called
        mock_tfhelper.apply.assert_called_once()

    def test_handle_update_operation(self, mock_deployment):
        """Test update operation handling."""
        backend_instance = MockStorageBackend()
        backend_name = "test-backend"
        config_updates = {"key1": "value1", "key2": "value2"}

        step = BaseStorageBackendConfigUpdateStep(
            mock_deployment, backend_instance, backend_name, config_updates
        )

        current_config = {
            "mock_backends": {
                "test-backend": {"charm_config": {"existing_key": "existing_value"}}
            }
        }
        updated_config = step.handle_update_operation(current_config)

        expected_config = {
            "mock_backends": {
                "test-backend": {
                    "charm_config": {
                        "existing_key": "existing_value",
                        "key1": "value1",
                        "key2": "value2",
                    }
                }
            }
        }
        assert updated_config == expected_config
