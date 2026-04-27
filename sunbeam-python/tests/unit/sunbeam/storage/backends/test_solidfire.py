# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for NetApp SolidFire backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestSolidFireBackend(BaseBackendTests):
    """Tests for NetApp SolidFire backend."""

    @pytest.fixture
    def backend(self, solidfire_backend):
        """Provide SolidFire backend instance."""
        return solidfire_backend

    def test_backend_type_is_solidfire(self, backend):
        """Test that backend type is 'solidfire'."""
        assert backend.backend_type == "solidfire"

    def test_display_name_mentions_solidfire(self, backend):
        """Test that display name mentions SolidFire."""
        assert "solidfire" in backend.display_name.lower()

    def test_charm_name_is_solidfire_charm(self, backend):
        """Test that charm name is cinder-volume-solidfire."""
        assert backend.charm_name == "cinder-volume-solidfire"

    def test_solidfire_config_has_required_fields(self, backend):
        """Test that SolidFire config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        required_fields = [
            "san_ip",
            "san_login",
            "san_password",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_solidfire_protocol_optional_iscsi(self, backend):
        """Test that protocol field is optional and accepts iscsi."""
        config_class = backend.config_type()

        config_minimal = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "user",
                "san-password": "secret",
            }
        )
        assert config_minimal.protocol is None

        config_iscsi = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "user",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert config_iscsi.protocol == "iscsi"

    def test_solidfire_credentials_are_secrets(self, backend):
        """Test that SAN login and password are marked as secrets."""
        config_class = backend.config_type()

        for fname in ("san_login", "san_password"):
            finfo = config_class.model_fields.get(fname)
            assert finfo is not None
            assert any(isinstance(m, SecretDictField) for m in finfo.metadata), (
                f"{fname} should be marked as secret"
            )

    def test_solidfire_provisioning_calc_enum_field_exists(self, backend):
        """Test that provisioning calculation field exists."""
        config_class = backend.config_type()
        assert "sf_provisioning_calc" in config_class.model_fields


class TestSolidFireConfigValidation:
    """Test SolidFire config validation behavior."""

    def test_protocol_rejects_invalid_value(self, solidfire_backend):
        """Test that protocol field rejects values other than iscsi."""
        config_class = solidfire_backend.config_type()

        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "user",
                    "san-password": "secret",
                    "protocol": "fc",
                }
            )

        assert "protocol" in str(exc_info.value).lower()
