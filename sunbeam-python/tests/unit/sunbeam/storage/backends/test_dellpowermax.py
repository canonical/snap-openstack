# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Dell PowerMax backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestDellpowermaxBackend(BaseBackendTests):
    """Tests for Dell PowerMax backend."""

    @pytest.fixture
    def backend(self, dellpowermax_backend):
        """Provide Dell PowerMax backend instance."""
        return dellpowermax_backend

    def test_backend_type_is_dellpowermax(self, backend):
        """Test that backend type is 'dellpowermax'."""
        assert backend.backend_type == "dellpowermax"

    def test_charm_name_is_dellpowermax_charm(self, backend):
        """Test that charm name is cinder-volume-dellpowermax."""
        assert backend.charm_name == "cinder-volume-dellpowermax"

    def test_config_has_required_fields(self, backend):
        """Test that Dell PowerMax config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "san_login", "san_password"):
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


class TestDellpowermaxConfigValidation:
    """Test Dell PowerMax config validation behavior."""

    def test_protocol_rejects_invalid_value(self, dellpowermax_backend):
        """Test that protocol rejects values other than fc/iscsi."""
        config_class = dellpowermax_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_fc(self, dellpowermax_backend):
        """Test that protocol accepts fc."""
        config_class = dellpowermax_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"

    def test_protocol_accepts_iscsi(self, dellpowermax_backend):
        """Test that protocol accepts iscsi."""
        config_class = dellpowermax_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
