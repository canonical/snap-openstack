# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for StorageBackendService class."""

from unittest.mock import Mock

import pytest

from sunbeam.clusterd.models import StorageBackend
from sunbeam.clusterd.service import StorageBackendNotFoundException
from sunbeam.storage.models import (
    BackendAlreadyExistsException,
    BackendNotFoundException,
    StorageBackendInfo,
)
from sunbeam.storage.service import StorageBackendService


@pytest.fixture
def mock_deployment():
    """Create a mock deployment."""
    deployment = Mock()
    deployment.openstack_machines_model = "openstack"
    deployment.juju_controller = "test-controller"
    deployment.get_client.return_value = Mock()
    return deployment


@pytest.fixture
def mock_jhelper():
    """Create a mock JujuHelper."""
    jhelper = Mock()
    jhelper.get_model_name_with_owner.return_value = "admin/openstack"

    # Mock model status
    mock_status = Mock()
    mock_status.apps = {}
    jhelper.get_model_status.return_value = mock_status

    return jhelper


@pytest.fixture
def service(mock_deployment, mock_jhelper):
    """Create a StorageBackendService instance."""
    return StorageBackendService(mock_deployment, mock_jhelper)


class TestStorageBackendService:
    """Tests for StorageBackendService."""

    def test_init(self, service, mock_deployment, mock_jhelper):
        """Test service initialization."""
        assert service.deployment == mock_deployment
        assert service.jhelper == mock_jhelper
        assert service.model == "admin/openstack"
        assert service._tfvar_config_key == "TerraformVarsStorageBackends"

    def test_list_backends_empty(self, service, mock_deployment):
        """Test listing backends when none exist."""
        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backends.return_value.root = []

        backends = service.list_backends()
        assert backends == []

    def test_list_backends_with_backends(self, service, mock_deployment, mock_jhelper):
        """Test listing backends with some backends present."""
        # Create mock backend data
        mock_backend = Mock(spec=StorageBackend)
        mock_backend.name = "test-backend"
        mock_backend.type = "test-type"
        mock_backend.config = {"key": "value"}

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backends.return_value.root = [mock_backend]

        # Mock Juju status
        mock_app_status = Mock()
        mock_app_status.app_status.current = "active"
        mock_app_status.charm = "test-charm"

        mock_status = Mock()
        mock_status.apps = {"test-backend": mock_app_status}
        mock_jhelper.get_model_status.return_value = mock_status

        backends = service.list_backends()

        assert len(backends) == 1
        assert isinstance(backends[0], StorageBackendInfo)
        assert backends[0].name == "test-backend"
        assert backends[0].backend_type == "test-type"
        assert backends[0].status == "active"
        assert backends[0].charm == "test-charm"
        assert backends[0].config == {"key": "value"}

    def test_list_backends_handles_errors(self, service, mock_deployment):
        """Test that list_backends handles errors gracefully."""
        mock_backend = Mock(spec=StorageBackend)
        mock_backend.name = "broken-backend"
        mock_backend.type = "test-type"
        # Make config access raise an error
        mock_backend.config = property(lambda self: (_ for _ in ()).throw(ValueError()))

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backends.return_value.root = [mock_backend]

        # Should not raise, just skip the broken backend
        backends = service.list_backends()
        assert len(backends) == 0

    def test_get_application_status_active(self, service, mock_jhelper):
        """Test getting application status for active app."""
        mock_app_status = Mock()
        mock_app_status.app_status.current = "active"

        mock_status = Mock()
        mock_status.apps = {"test-app": mock_app_status}
        mock_jhelper.get_model_status.return_value = mock_status

        status = service._get_application_status(mock_jhelper, "test-app")
        assert status == "active"

    def test_get_application_status_not_found(self, service, mock_jhelper):
        """Test getting application status for non-existent app."""
        mock_status = Mock()
        mock_status.apps = {}
        mock_jhelper.get_model_status.return_value = mock_status

        status = service._get_application_status(mock_jhelper, "nonexistent")
        assert status == "not-found"

    def test_get_application_status_error(self, service, mock_jhelper):
        """Test getting application status when Juju errors."""
        mock_jhelper.get_model_status.side_effect = Exception("Juju error")

        status = service._get_application_status(mock_jhelper, "test-app")
        assert status == "unknown"

    def test_get_application_charm_success(self, service, mock_jhelper):
        """Test getting application charm successfully."""
        mock_app_status = Mock()
        mock_app_status.charm = "ch:amd64/focal/test-charm-123"

        mock_status = Mock()
        mock_status.apps = {"test-app": mock_app_status}
        mock_jhelper.get_model_status.return_value = mock_status

        charm = service._get_application_charm(mock_jhelper, "test-app")
        assert charm == "ch:amd64/focal/test-charm-123"

    def test_get_application_charm_not_found(self, service, mock_jhelper):
        """Test getting charm for non-existent app."""
        mock_status = Mock()
        mock_status.apps = {}
        mock_jhelper.get_model_status.return_value = mock_status

        charm = service._get_application_charm(mock_jhelper, "nonexistent")
        assert charm == "Not Found"

    def test_get_application_charm_error(self, service, mock_jhelper):
        """Test getting charm when Juju errors."""
        mock_jhelper.get_model_status.side_effect = Exception("Juju error")

        charm = service._get_application_charm(mock_jhelper, "test-app")
        assert charm == "Unknown"

    def test_backend_exists_true(self, service, mock_deployment):
        """Test checking if backend exists - true case."""
        mock_backend = Mock(spec=StorageBackend)
        mock_backend.type = "test-type"

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backend.return_value = mock_backend

        exists = service.backend_exists("test-backend", "test-type")
        assert exists is True

    def test_backend_exists_false(self, service, mock_deployment):
        """Test checking if backend exists - false case."""
        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backend.side_effect = (
            StorageBackendNotFoundException()
        )

        exists = service.backend_exists("nonexistent", "test-type")
        assert exists is False

    def test_backend_exists_type_mismatch(self, service, mock_deployment):
        """Test checking backend exists with type mismatch."""
        mock_backend = Mock(spec=StorageBackend)
        mock_backend.type = "different-type"

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backend.return_value = mock_backend

        with pytest.raises(BackendAlreadyExistsException):
            service.backend_exists("test-backend", "expected-type")

    def test_get_backend_success(self, service, mock_deployment):
        """Test getting a backend successfully."""
        mock_backend = Mock(spec=StorageBackend)
        mock_backend.name = "test-backend"
        mock_backend.type = "test-type"

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backend.return_value = mock_backend

        backend = service.get_backend("test-backend")
        assert backend == mock_backend

    def test_get_backend_not_found(self, service, mock_deployment):
        """Test getting a non-existent backend."""
        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backend.side_effect = (
            StorageBackendNotFoundException()
        )

        with pytest.raises(BackendNotFoundException):
            service.get_backend("nonexistent")

    def test_multiple_backends_different_types(
        self, service, mock_deployment, mock_jhelper
    ):
        """Test listing multiple backends of different types."""
        mock_backend1 = Mock(spec=StorageBackend)
        mock_backend1.name = "backend1"
        mock_backend1.type = "type1"
        mock_backend1.config = {}

        mock_backend2 = Mock(spec=StorageBackend)
        mock_backend2.name = "backend2"
        mock_backend2.type = "type2"
        mock_backend2.config = {}

        mock_client = mock_deployment.get_client.return_value
        mock_client.cluster.get_storage_backends.return_value.root = [
            mock_backend1,
            mock_backend2,
        ]

        # Mock Juju status for both apps
        mock_app_status1 = Mock()
        mock_app_status1.app_status.current = "active"
        mock_app_status1.charm = "charm1"

        mock_app_status2 = Mock()
        mock_app_status2.app_status.current = "waiting"
        mock_app_status2.charm = "charm2"

        mock_status = Mock()
        mock_status.apps = {"backend1": mock_app_status1, "backend2": mock_app_status2}
        mock_jhelper.get_model_status.return_value = mock_status

        backends = service.list_backends()

        assert len(backends) == 2
        assert backends[0].name == "backend1"
        assert backends[0].backend_type == "type1"
        assert backends[0].status == "active"
        assert backends[1].name == "backend2"
        assert backends[1].backend_type == "type2"
        assert backends[1].status == "waiting"
