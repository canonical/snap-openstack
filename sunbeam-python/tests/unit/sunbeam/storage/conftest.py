# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test fixtures for storage backend tests."""

from unittest.mock import Mock, PropertyMock

import pytest

from sunbeam.clusterd.client import Client
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformHelper
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import StorageBackendConfig


@pytest.fixture
def mock_deployment():
    """Mock deployment object."""
    deployment = Mock()
    deployment.openstack_machines_model = "openstack"  # Match actual service behavior

    # Mock the client with cluster attribute
    mock_client = Mock()
    mock_cluster = Mock()
    mock_client.cluster = mock_cluster
    deployment.get_client.return_value = mock_client

    # Mock juju_controller
    mock_controller = Mock()
    mock_controller.name = "test-controller"
    type(deployment).juju_controller = PropertyMock(return_value=mock_controller)

    # Mock get_tfhelper
    deployment.get_tfhelper.return_value = Mock(spec=TerraformHelper)

    return deployment


@pytest.fixture
def mock_client():
    """Mock clusterd client."""
    client = Mock(spec=Client)
    client.cluster = Mock()
    return client


@pytest.fixture
def mock_tfhelper():
    """Mock Terraform helper."""
    return Mock(spec=TerraformHelper)


@pytest.fixture
def mock_jhelper():
    """Mock Juju helper."""
    return Mock()


@pytest.fixture
def mock_manifest():
    """Mock manifest object."""
    return Mock(spec=Manifest)


@pytest.fixture
def sample_backend_config():
    """Sample backend configuration for testing."""
    return {
        "model": "openstack",
        "hitachi_backends": {
            "test-backend": {
                "backend_type": "hitachi",
                "charm_name": "cinder-volume-hitachi",
                "charm_channel": "latest/edge",
                "backend_config": {
                    "hitachi-storage-id": "123456",
                    "hitachi-pools": ["pool1", "pool2"],
                    "san-ip": "192.168.1.100",
                },
                "backend_endpoint": "cinder-volume",
                "units": 1,
                "additional_integrations": {},
            }
        },
    }


@pytest.fixture
def sample_clusterd_config():
    """Sample clusterd configuration for testing."""
    return {
        "TerraformVarsStorageBackends": {
            "hitachi_backends": {
                "test-backend": {
                    "backend_type": "hitachi",
                    "charm_name": "cinder-volume-hitachi",
                    "charm_channel": "latest/edge",
                    "charm_config": {
                        "hitachi-storage-id": "123456",
                        "hitachi-pools": ["pool1", "pool2"],
                        "san-ip": "192.168.1.100",
                    },
                    "backend_endpoint": "cinder-volume",
                    "units": 1,
                    "additional_integrations": {},
                }
            }
        }
    }


@pytest.fixture
def mock_storage_backend():
    """Mock storage backend for testing."""

    class MockStorageBackend(StorageBackendBase):
        """Mock storage backend for testing."""

        name = "mock"
        display_name = "Mock Storage Backend"
        charm_name = "mock-charm"

        def __init__(self):
            super().__init__()
            self.tfplan = "mock-backend-plan"
            self.tfplan_dir = "deploy-mock-backend"

        @property
        def config_class(self):
            return StorageBackendConfig

        def get_terraform_variables(
            self, backend_name: str, config: StorageBackendConfig, model: str
        ):
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
            """Create a mock deploy step."""
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
                backend_config,
                self,
                model,
            )

        def create_destroy_step(
            self, deployment, client, tfhelper, jhelper, manifest, backend_name, model
        ):
            """Create a mock destroy step."""
            from sunbeam.storage.steps import BaseStorageBackendDestroyStep

            return BaseStorageBackendDestroyStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                backend_name,
                self,
                model,
            )

        def create_config_update_step(
            self,
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            backend_name,
            backend_config,
            model,
            updates,
        ):
            """Create a mock config update step."""
            from sunbeam.storage.steps import BaseStorageBackendConfigUpdateStep

            return BaseStorageBackendConfigUpdateStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                backend_name,
                backend_config,
                self,
                model,
                updates,
            )

        def create_update_config_step(self, deployment, backend_name, config_updates):
            """Create a configuration update step for this backend."""
            # Mock implementation - return a simple mock step

            from sunbeam.core.common import BaseStep

            class MockUpdateConfigStep(BaseStep):
                def run(self):
                    from sunbeam.core.common import ResultType

                    return ResultType.COMPLETED

            return MockUpdateConfigStep()

        def commands(self):
            """Return mock commands for testing."""
            return {
                "add": [{"name": "mock", "command": Mock()}],
                "remove": [{"name": "mock", "command": Mock()}],
                "list": [{"name": "mock", "command": Mock()}],
                "config": [{"name": "mock", "command": Mock()}],
            }

        def register_add_cli(self, add):
            """Mock CLI registration."""
            pass

        def register_cli(
            self,
            remove,
            config_show,
            config_set,
            config_reset,
            config_options,
            deployment,
        ):
            """Mock CLI registration."""
            pass

        def prompt_for_config(self, backend_name: str) -> StorageBackendConfig:
            """Mock prompt for configuration."""
            return StorageBackendConfig(name=backend_name)

    return MockStorageBackend()
