# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Inspur InStorage backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestInspurinstorageBackend(BaseBackendTests):
    """Tests for Inspur InStorage backend."""

    @pytest.fixture
    def backend(self, inspurinstorage_backend):
        """Provide Inspur InStorage backend instance."""
        return inspurinstorage_backend

    def test_backend_type_is_inspurinstorage(self, backend):
        """Test that backend type is 'inspurinstorage'."""
        assert backend.backend_type == "inspurinstorage"

    def test_charm_name_is_inspurinstorage_charm(self, backend):
        """Test that charm name is cinder-volume-inspurinstorage."""
        assert backend.charm_name == "cinder-volume-inspurinstorage"

    def test_config_has_required_fields(self, backend):
        """Test that InStorage config has required fields."""
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


class TestInspurinstorageConfigValidation:
    """Test Inspur InStorage config validation behavior."""

    def test_protocol_rejects_invalid_value(self, inspurinstorage_backend):
        """Test that protocol rejects values other than fc/iscsi."""
        config_class = inspurinstorage_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_fc(self, inspurinstorage_backend):
        """Test that protocol accepts fc."""
        config_class = inspurinstorage_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"
