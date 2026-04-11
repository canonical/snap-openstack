# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for internal-ceph storage backend."""

from unittest.mock import MagicMock

import pytest

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.backends.internal_ceph.backend import (
    InternalCephBackend,
    InternalCephConfig,
)
from sunbeam.storage.base import (
    BackendIntegration,
    HypervisorIntegration,
    StorageBackendBase,
)


@pytest.fixture
def internal_ceph_backend():
    """Provide an InternalCephBackend instance."""
    return InternalCephBackend()


@pytest.fixture
def mock_deployment():
    """Provide a mock deployment for endpoint binding tests."""
    deployment = MagicMock()
    deployment.get_space.side_effect = lambda net: f"space-{net.value}"
    return deployment


class TestInternalCephBackendAttributes:
    """Tests for InternalCephBackend class attributes and properties."""

    def test_backend_type(self, internal_ceph_backend):
        """Test that backend_type is 'internal-ceph'."""
        assert internal_ceph_backend.backend_type == "internal-ceph"

    def test_display_name(self, internal_ceph_backend):
        """Test that display_name is set."""
        assert internal_ceph_backend.display_name == "Internal Ceph"

    def test_generally_available(self, internal_ceph_backend):
        """Test that generally_available is True."""
        assert internal_ceph_backend.generally_available is True

    def test_is_storage_backend_base(self, internal_ceph_backend):
        """Test that backend inherits from StorageBackendBase."""
        assert isinstance(internal_ceph_backend, StorageBackendBase)

    def test_charm_name(self, internal_ceph_backend):
        """Test that charm_name is 'cinder-volume-ceph'."""
        assert internal_ceph_backend.charm_name == "cinder-volume-ceph"

    def test_charm_channel(self, internal_ceph_backend):
        """Test that charm_channel is set."""
        assert internal_ceph_backend.charm_channel == "2024.1/stable"

    def test_charm_base(self, internal_ceph_backend):
        """Test that charm_base is ubuntu@24.04."""
        assert internal_ceph_backend.charm_base == "ubuntu@24.04"

    def test_supports_ha(self, internal_ceph_backend):
        """Test that supports_ha is True."""
        assert internal_ceph_backend.supports_ha is True

    def test_principal_application(self, internal_ceph_backend):
        """Test that principal_application is 'cinder-volume' (HA)."""
        assert internal_ceph_backend.principal_application == "cinder-volume"

    def test_application_name(self, internal_ceph_backend):
        """Test that the backend keeps the legacy subordinate app name."""
        assert internal_ceph_backend.get_application_name("internal-ceph") == (
            "cinder-volume-ceph"
        )

    def test_units(self, internal_ceph_backend):
        """Test that internal Ceph is modeled as a subordinate app."""
        assert internal_ceph_backend.get_units() is None

    def test_config_type_returns_internal_ceph_config(self, internal_ceph_backend):
        """Test that config_type() returns InternalCephConfig."""
        assert internal_ceph_backend.config_type() is InternalCephConfig

    def test_config_type_is_storage_backend_config_subclass(
        self, internal_ceph_backend
    ):
        """Test that config_type() returns a StorageBackendConfig subclass."""
        config_class = internal_ceph_backend.config_type()
        assert issubclass(config_class, StorageBackendConfig)


class TestInternalCephConfig:
    """Tests for InternalCephConfig model."""

    def test_default_replication_count(self):
        """Test that default ceph_osd_replication_count is 1."""
        config = InternalCephConfig()
        assert config.ceph_osd_replication_count == 1

    def test_custom_replication_count(self):
        """Test creating config with custom replication count."""
        config = InternalCephConfig.model_validate({"ceph-osd-replication-count": 3})
        assert config.ceph_osd_replication_count == 3

    def test_config_is_pydantic_model(self):
        """Test that InternalCephConfig is a Pydantic model."""
        from pydantic import BaseModel

        assert issubclass(InternalCephConfig, BaseModel)

    def test_config_is_storage_backend_config(self):
        """Test that InternalCephConfig extends StorageBackendConfig."""
        assert issubclass(InternalCephConfig, StorageBackendConfig)


class TestInternalCephIntegrations:
    """Tests for integration methods."""

    def test_get_extra_integrations(self, internal_ceph_backend, mock_deployment):
        """Test that get_extra_integrations returns microceph ceph integration."""
        integrations = internal_ceph_backend.get_extra_integrations(mock_deployment)
        assert len(integrations) == 1

        integration = next(iter(integrations))
        assert isinstance(integration, BackendIntegration)
        assert integration.application_name == "microceph"
        assert integration.endpoint_name == "ceph"
        assert integration.backend_endpoint_name == "ceph"

    def test_get_hypervisor_integrations(self, internal_ceph_backend, mock_deployment):
        """Test that get_hypervisor_integrations returns ceph-access integration."""
        integrations = internal_ceph_backend.get_hypervisor_integrations(
            mock_deployment
        )
        assert len(integrations) == 1

        integration = next(iter(integrations))
        assert isinstance(integration, HypervisorIntegration)
        assert integration.application_name == "cinder-volume-ceph"
        assert integration.endpoint_name == "ceph-access"
        assert integration.hypervisor_endpoint_name == "ceph-access"

    def test_get_endpoint_bindings(self, internal_ceph_backend, mock_deployment):
        """Test endpoint bindings match the original cinder_volume_ceph."""
        bindings = internal_ceph_backend.get_endpoint_bindings(mock_deployment)

        # Should have default space, ceph-access, and ceph bindings
        endpoints = {b.get("endpoint"): b.get("space") for b in bindings}

        # default space on MANAGEMENT
        assert endpoints[None] == "space-management"
        # ceph-access on MANAGEMENT space
        assert endpoints["ceph-access"] == "space-management"
        # ceph on STORAGE space
        assert endpoints["ceph"] == "space-storage"
        # No extra bindings
        assert len(bindings) == 3
