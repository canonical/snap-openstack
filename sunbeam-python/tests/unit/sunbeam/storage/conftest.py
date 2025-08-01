# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.core.deployment import Deployment


@pytest.fixture
def mock_deployment():
    """Fixture providing a mock deployment object."""
    deployment = Mock(spec=Deployment)
    deployment.juju_controller = "test-controller"
    deployment.openstack_machines_model = "test-model"
    return deployment


@pytest.fixture
def mock_juju_helper():
    """Fixture providing a mock JujuHelper."""
    with patch("sunbeam.storage.basestorage.ExtendedJujuHelper") as mock_helper_class:
        mock_helper = Mock()
        mock_helper_class.return_value = mock_helper
        yield mock_helper


@pytest.fixture
def mock_storage_service():
    """Fixture providing a mock StorageBackendService."""
    with patch(
        "sunbeam.storage.basestorage.StorageBackendService"
    ) as mock_service_class:
        mock_service = Mock()
        mock_service_class.return_value = mock_service
        yield mock_service


@pytest.fixture(autouse=True)
def reset_global_registry():
    """Fixture to reset the global registry state between tests."""
    from sunbeam.storage.registry import storage_backend_registry

    # Store original state
    original_backends = storage_backend_registry._backends.copy()
    original_loaded = storage_backend_registry._loaded

    # Reset for test
    storage_backend_registry._backends = {}
    storage_backend_registry._loaded = False

    yield

    # Restore original state
    storage_backend_registry._backends = original_backends
    storage_backend_registry._loaded = original_loaded
