# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Zadara backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestZadaraBackend(BaseBackendTests):
    """Tests for Zadara backend."""

    @pytest.fixture
    def backend(self, zadara_backend):
        """Provide Zadara backend instance."""
        return zadara_backend

    def test_backend_type_is_zadara(self, backend):
        """Test that backend type is 'zadara'."""
        assert backend.backend_type == "zadara"

    def test_charm_name_is_zadara_charm(self, backend):
        """Test that charm name is cinder-volume-zadara."""
        assert backend.charm_name == "cinder-volume-zadara"

    def test_config_has_required_fields(self, backend):
        """Test that Zadara config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol", "zadara_access_key"):
            assert field in fields, f"Required field {field} not found in config"

    def test_sensitive_fields_are_marked_secret(self, backend):
        """Test that access key field is marked as secret."""
        config_class = backend.config_type()
        field = config_class.model_fields.get("zadara_access_key")
        assert field is not None
        assert any(isinstance(m, SecretDictField) for m in field.metadata), (
            "zadara_access_key should be marked as secret"
        )


class TestZadaraConfigValidation:
    """Test Zadara config validation behavior."""

    def test_protocol_rejects_invalid_value(self, zadara_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = zadara_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "zadara-access-key": "secret",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, zadara_backend):
        """Test that protocol accepts iscsi."""
        config_class = zadara_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "zadara-access-key": "secret",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
