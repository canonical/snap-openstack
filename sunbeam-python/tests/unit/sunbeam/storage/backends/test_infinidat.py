# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for INFINIDAT backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestInfinidatBackend(BaseBackendTests):
    """Tests for INFINIDAT backend."""

    @pytest.fixture
    def backend(self, infinidat_backend):
        """Provide INFINIDAT backend instance."""
        return infinidat_backend

    def test_backend_type_is_infinidat(self, backend):
        """Test that backend type is 'infinidat'."""
        assert backend.backend_type == "infinidat"

    def test_charm_name_is_infinidat_charm(self, backend):
        """Test that charm name is cinder-volume-infinidat."""
        assert backend.charm_name == "cinder-volume-infinidat"

    def test_config_has_required_fields(self, backend):
        """Test that INFINIDAT config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "san_login", "san_password", "protocol"):
            assert field in fields, f"Required field {field} not found in config"

    def test_san_credentials_are_secret(self, backend):
        """Test that SAN login and password are marked as secrets."""
        config_class = backend.config_type()
        for field_name in ("san_login", "san_password"):
            field = config_class.model_fields.get(field_name)
            assert field is not None
            assert any(isinstance(m, SecretDictField) for m in field.metadata), (
                f"{field_name} should be marked as secret"
            )


class TestInfinidatConfigValidation:
    """Test INFINIDAT config validation behavior."""

    def test_protocol_rejects_invalid_value(self, infinidat_backend):
        """Test that protocol rejects values other than iscsi/fc."""
        config_class = infinidat_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_iscsi(self, infinidat_backend):
        """Test that protocol accepts iscsi."""
        config_class = infinidat_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
