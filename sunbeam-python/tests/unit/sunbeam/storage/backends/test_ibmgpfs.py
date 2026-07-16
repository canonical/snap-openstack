# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for IBM GPFS backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestIbmgpfsBackend(BaseBackendTests):
    """Tests for IBM GPFS backend."""

    @pytest.fixture
    def backend(self, ibmgpfs_backend):
        """Provide IBM GPFS backend instance."""
        return ibmgpfs_backend

    def test_backend_type_is_ibmgpfs(self, backend):
        """Test that backend type is 'ibmgpfs'."""
        assert backend.backend_type == "ibmgpfs"

    def test_charm_name_is_ibmgpfs_charm(self, backend):
        """Test that charm name is cinder-volume-ibmgpfs."""
        assert backend.charm_name == "cinder-volume-ibmgpfs"

    def test_config_has_required_fields(self, backend):
        """Test that IBM GPFS config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "san_login", "san_password", "protocol"):
            assert field in fields, f"Required field {field} not found in config"

    def test_credentials_are_secret(self, backend):
        """Test that configured credentials are marked as secrets."""
        config_class = backend.config_type()
        for field_name in ("san_login", "san_password", "gpfs_user_password"):
            field = config_class.model_fields.get(field_name)
            assert field is not None
            assert any(isinstance(m, SecretDictField) for m in field.metadata), (
                f"{field_name} should be marked as secret"
            )


class TestIbmgpfsConfigValidation:
    """Test IBM GPFS config validation behavior."""

    def test_gpfs_login_is_required(self, ibmgpfs_backend):
        """Test that gpfs-user-login is required."""
        config_class = ibmgpfs_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "gpfs-user-password": "secret",
                    "protocol": "iscsi",
                }
            )

    def test_protocol_rejects_invalid_value(self, ibmgpfs_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = ibmgpfs_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "gpfs-user-password": "secret",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, ibmgpfs_backend):
        """Test that protocol accepts iscsi."""
        config_class = ibmgpfs_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "gpfs-user-login": "gpfs-admin",
                "gpfs-user-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
