# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Nexenta backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestNexentaBackend(BaseBackendTests):
    """Tests for Nexenta backend."""

    @pytest.fixture
    def backend(self, nexenta_backend):
        """Provide Nexenta backend instance."""
        return nexenta_backend

    def test_backend_type_is_nexenta(self, backend):
        """Test that backend type is 'nexenta'."""
        assert backend.backend_type == "nexenta"

    def test_charm_name_is_nexenta_charm(self, backend):
        """Test that charm name is cinder-volume-nexenta."""
        assert backend.charm_name == "cinder-volume-nexenta"

    def test_config_has_required_fields(self, backend):
        """Test that Nexenta config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol", "nexenta_rest_password"):
            assert field in fields, f"Required field {field} not found in config"

    def test_password_is_marked_secret(self, backend):
        """Test that Nexenta REST password is marked as secret."""
        config_class = backend.config_type()
        field = config_class.model_fields.get("nexenta_rest_password")
        assert field is not None
        assert any(isinstance(m, SecretDictField) for m in field.metadata), (
            "nexenta_rest_password should be marked as secret"
        )


class TestNexentaConfigValidation:
    """Test Nexenta config validation behavior."""

    def test_protocol_rejects_invalid_value(self, nexenta_backend):
        """Test that protocol rejects values other than iscsi/nvme."""
        config_class = nexenta_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "nexenta-rest-password": "secret",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, nexenta_backend):
        """Test that protocol accepts iscsi."""
        config_class = nexenta_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "nexenta-rest-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
