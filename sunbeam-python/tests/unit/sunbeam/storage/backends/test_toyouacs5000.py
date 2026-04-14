# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Toyou ACS5000 backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestToyouacs5000Backend(BaseBackendTests):
    """Tests for Toyou ACS5000 backend."""

    @pytest.fixture
    def backend(self, toyouacs5000_backend):
        """Provide Toyou ACS5000 backend instance."""
        return toyouacs5000_backend

    def test_backend_type_is_toyouacs5000(self, backend):
        """Test that backend type is 'toyouacs5000'."""
        assert backend.backend_type == "toyouacs5000"

    def test_charm_name_is_toyouacs5000_charm(self, backend):
        """Test that charm name is cinder-volume-toyouacs5000."""
        assert backend.charm_name == "cinder-volume-toyouacs5000"

    def test_config_has_required_fields(self, backend):
        """Test that Toyou ACS5000 config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol", "san_login", "san_password"):
            assert field in fields, f"Required field {field} not found in config"

    def test_sensitive_fields_are_marked_secret(self, backend):
        """Test that SAN credentials are marked as secret."""
        config_class = backend.config_type()
        for field_name in ("san_login", "san_password"):
            field = config_class.model_fields.get(field_name)
            assert field is not None
            assert any(isinstance(m, SecretDictField) for m in field.metadata), (
                f"{field_name} should be marked as secret"
            )


class TestToyouacs5000ConfigValidation:
    """Test Toyou ACS5000 config validation behavior."""

    def test_protocol_rejects_invalid_value(self, toyouacs5000_backend):
        """Test that protocol rejects values other than fc/iscsi."""
        config_class = toyouacs5000_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_fc(self, toyouacs5000_backend):
        """Test that protocol accepts fc."""
        config_class = toyouacs5000_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"
