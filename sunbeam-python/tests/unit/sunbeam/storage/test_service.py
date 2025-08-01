# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for storage backend service layer."""

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.storage.models import (
    BackendNotFoundException,
    StorageBackendException,
)
from sunbeam.storage.service import StorageBackendService


class TestStorageBackendService:
    """Test cases for StorageBackendService class."""

    def test_init(self, mock_deployment):
        """Test service initialization."""
        service = StorageBackendService(mock_deployment)

        assert service.deployment == mock_deployment
        assert service.model == "admin/openstack"
        assert service._tfvar_config_key == "TerraformVarsStorageBackends"

    def test_get_model_name_with_admin_prefix(self, mock_deployment):
        """Test model name retrieval when model already has admin prefix."""
        mock_deployment.openstack_machines_model = "admin/openstack"
        service = StorageBackendService(mock_deployment)

        assert service.model == "admin/openstack"

    def test_get_model_name_without_admin_prefix(self, mock_deployment):
        """Test model name retrieval when missing admin prefix."""
        mock_deployment.openstack_machines_model = "openstack"
        service = StorageBackendService(mock_deployment)

        assert service.model == "admin/openstack"

    def test_list_backends_success(self, mock_deployment, sample_clusterd_config):
        """Test successful backend listing."""
        import json

        mock_client = mock_deployment.get_client.return_value
        # read_config expects JSON string, not dict
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)
        backends = service.list_backends()

        assert len(backends) == 1
        assert backends[0].name == "test-backend"
        assert backends[0].backend_type == "hitachi"
        assert (
            backends[0].status == "active"
        )  # Service returns 'active' for Terraform-managed backends
        assert backends[0].charm == "cinder-volume-hitachi"

    def test_list_backends_no_config(self, mock_deployment):
        """Test backend listing when no configuration exists."""
        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.side_effect = ConfigItemNotFoundException("test")

        service = StorageBackendService(mock_deployment)
        backends = service.list_backends()

        assert backends == []

    def test_list_backends_empty_config(self, mock_deployment):
        """Test backend listing with empty configuration."""
        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = {
            "TerraformVarsStorageBackends": {}
        }

        service = StorageBackendService(mock_deployment)
        backends = service.list_backends()

        assert backends == []

    def test_list_backends_no_backends_key(self, mock_deployment):
        """Test backend listing when backends key is missing."""
        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = {
            "TerraformVarsStorageBackends": {"model": "openstack"}
        }

        service = StorageBackendService(mock_deployment)
        backends = service.list_backends()

        assert backends == []

    def test_backend_exists_true(self, mock_deployment, sample_clusterd_config):
        """Test backend existence check when backend exists."""
        import json

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)
        exists = service.backend_exists("test-backend", "hitachi")

        assert exists is True

    def test_backend_exists_false(self, mock_deployment, sample_clusterd_config):
        """Test backend existence check when backend doesn't exist."""
        import json

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)
        result = service.backend_exists("nonexistent-backend", "hitachi")

        assert result is False

    def test_backend_exists_no_config(self, mock_deployment):
        """Test backend existence check when no configuration exists."""
        from sunbeam.clusterd.service import ConfigItemNotFoundException

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.side_effect = ConfigItemNotFoundException(
            "Config not found"
        )

        service = StorageBackendService(mock_deployment)
        result = service.backend_exists("test-backend", "hitachi")

        assert result is False

    def test_get_backend_config_success(self, mock_deployment, sample_clusterd_config):
        """Test successful backend config retrieval."""
        import json

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)
        config = service.get_backend_config("test-backend", "hitachi")

        assert isinstance(config, dict)
        assert "hitachi-storage-id" in config
        assert config["hitachi-storage-id"] == "123456"

    def test_get_backend_config_not_found(
        self, mock_deployment, sample_clusterd_config
    ):
        """Test backend config retrieval for non-existent backend."""
        import json

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)

        with pytest.raises(BackendNotFoundException):
            service.get_backend_config("nonexistent-backend", "hitachi")

    def test_get_backend_config_no_config(self, mock_deployment):
        """Test backend config retrieval when no configuration exists."""
        from sunbeam.clusterd.service import ConfigItemNotFoundException

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.side_effect = ConfigItemNotFoundException(
            "Config not found"
        )

        service = StorageBackendService(mock_deployment)

        with pytest.raises(BackendNotFoundException):
            service.get_backend_config("test-backend", "hitachi")

    def test_set_backend_config_success(self, mock_deployment, sample_clusterd_config):
        """Test successful backend configuration update."""
        import json

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)
        # set_backend_config doesn't raise exceptions for existing backends
        service.set_backend_config("test-backend", "hitachi", {"new-option": "value"})

        # Test passes if no exception is raised

    def test_set_backend_config_not_found(
        self, mock_deployment, sample_clusterd_config
    ):
        """Test backend configuration update for non-existent backend."""
        import json

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)

        with pytest.raises(BackendNotFoundException):
            service.set_backend_config(
                "nonexistent-backend", "hitachi", {"new-option": "value"}
            )

    def test_reset_backend_config_success(
        self, mock_deployment, sample_clusterd_config
    ):
        """Test successful backend configuration reset."""
        import json

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)
        # reset_backend_config doesn't raise exceptions for existing backends
        service.reset_backend_config("test-backend", "hitachi", ["hitachi-storage-id"])

        # Test passes if no exception is raised

    def test_reset_backend_config_not_found(
        self, mock_deployment, sample_clusterd_config
    ):
        """Test configuration reset for non-existent backend."""
        import json

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.return_value = json.dumps(
            sample_clusterd_config["TerraformVarsStorageBackends"]
        )

        service = StorageBackendService(mock_deployment)

        with pytest.raises(BackendNotFoundException):
            service.reset_backend_config(
                "nonexistent-backend", "hitachi", ["hitachi-storage-id"]
            )

    def test_error_handling_client_exception(self, mock_deployment):
        """Test error handling when client raises exception."""
        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.side_effect = Exception("Client error")

        service = StorageBackendService(mock_deployment)

        # The service logs the error but returns empty list instead of raising exception
        backends = service.list_backends()
        assert backends == []

    def test_error_handling_set_config_exception(
        self, mock_deployment, sample_clusterd_config
    ):
        """Test error handling when set config raises exception."""
        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_config.side_effect = Exception("Config error")

        service = StorageBackendService(mock_deployment)

        with pytest.raises(StorageBackendException):
            service.set_backend_config("test-backend", "hitachi", {"key": "value"})
