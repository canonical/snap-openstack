# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Inspur AS13000 backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestInspuras13000Backend(BaseBackendTests):
    """Tests for Inspur AS13000 backend."""

    @pytest.fixture
    def backend(self, inspuras13000_backend):
        """Provide Inspur AS13000 backend instance."""
        return inspuras13000_backend

    def test_backend_type_is_inspuras13000(self, backend):
        """Test that backend type is 'inspuras13000'."""
        assert backend.backend_type == "inspuras13000"

    def test_charm_name_is_inspuras13000_charm(self, backend):
        """Test that charm name is cinder-volume-inspuras13000."""
        assert backend.charm_name == "cinder-volume-inspuras13000"

    def test_config_has_required_fields(self, backend):
        """Test that AS13000 config has required fields."""
        fields = backend.config_type().model_fields
        for field in (
            "san_ip",
            "san_login",
            "san_password",
            "as13000_token_available_time",
            "protocol",
        ):
            assert field in fields, f"Required field {field} not found in config"

    def test_credentials_are_secret(self, backend):
        """Test that credential-like fields are marked as secrets."""
        config_class = backend.config_type()
        for field_name in ("san_login", "san_password", "as13000_token_available_time"):
            field = config_class.model_fields.get(field_name)
            assert field is not None
            assert any(isinstance(m, SecretDictField) for m in field.metadata), (
                f"{field_name} should be marked as secret"
            )


class TestInspuras13000ConfigValidation:
    """Test AS13000 config validation behavior."""

    def test_protocol_rejects_invalid_value(self, inspuras13000_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = inspuras13000_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "as13000-token-available-time": "3600",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, inspuras13000_backend):
        """Test that protocol accepts iscsi."""
        config_class = inspuras13000_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "as13000-token-available-time": "3600",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
