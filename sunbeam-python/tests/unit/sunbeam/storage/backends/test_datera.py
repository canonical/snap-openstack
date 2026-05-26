# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Datera backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestDateraBackend(BaseBackendTests):
    """Tests for Datera backend."""

    @pytest.fixture
    def backend(self, datera_backend):
        """Provide Datera backend instance."""
        return datera_backend

    def test_backend_type_is_datera(self, backend):
        """Test that backend type is 'datera'."""
        assert backend.backend_type == "datera"

    def test_charm_name_is_datera_charm(self, backend):
        """Test that charm name is cinder-volume-datera."""
        assert backend.charm_name == "cinder-volume-datera"

    def test_datera_config_has_expected_fields(self, backend):
        """Test that Datera config defines all expected fields."""
        fields = backend.config_type().model_fields
        expected_fields = ["san_ip", "san_login", "san_password", "protocol"]
        for field in expected_fields:
            assert field in fields, f"Expected field {field} not found in config"

    def test_datera_credentials_are_secrets(self, backend):
        """Test that SAN login and password are marked as secrets."""
        config_class = backend.config_type()
        for field_name in ("san_login", "san_password"):
            field = config_class.model_fields.get(field_name)
            assert field is not None
            assert any(isinstance(m, SecretDictField) for m in field.metadata), (
                f"{field_name} should be marked as secret"
            )


class TestDateraConfigValidation:
    """Test Datera config validation behavior."""

    def test_requires_mandatory_fields(self, datera_backend):
        """Test that required SAN connection fields are enforced."""
        config_class = datera_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate({})

    def test_accepts_valid_minimal_config(self, datera_backend):
        """Test that a minimal valid Datera config is accepted."""
        config_class = datera_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.10",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"

    def test_defaults_protocol_to_iscsi(self, datera_backend):
        """Test that protocol defaults to iscsi when omitted."""
        config_class = datera_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.10",
                "san-login": "admin",
                "san-password": "secret",
            }
        )
        assert config.protocol == "iscsi"
