# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Dell PowerStore Center backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestDellpowerstoreBackend(BaseBackendTests):
    """Tests for Dell PowerStore backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, dellpowerstore_backend):
        """Provide Dell PowerStore backend instance."""
        return dellpowerstore_backend

    # Backend-specific tests

    def test_backend_type_is_dellpowerstore(self, backend):
        """Test that backend type is 'dellpowerstore'."""
        assert backend.backend_type == "dellpowerstore"

    def test_display_name_mentions_dell(self, backend):
        """Test that display name mentions Dell."""
        assert "dell" in backend.display_name.lower()

    def test_charm_name_is_dellpowerstore_charm(self, backend):
        """Test that charm name is cinder-volume-dellpowerstore."""
        assert backend.charm_name == "cinder-volume-dellpowerstore"

    def test_dellpowerstore_config_has_required_fields(self, backend):
        """Test that Dell PowerStore config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        # Verify Dell PowerStore specific required fields
        required_fields = [
            "san_ip",
            "san_username",
            "san_password",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_dellpowerstore_protocol_is_optional_literal(self, backend):
        """Test that protocol field accepts fc or iscsi."""
        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None

        # Test config without protocol (optional)
        config_no_protocol = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
            }
        )
        assert config_no_protocol.protocol == "fc"

        # Test valid config with fc
        valid_config_fc = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert valid_config_fc.protocol == "fc"

        # Test valid config with iscsi
        valid_config_iscsi = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert valid_config_iscsi.protocol == "iscsi"

    def test_dellpowerstore_san_credentials_are_secret(self, backend):
        """Test that SAN credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check san_username is marked as secret
        username_field = config_class.model_fields.get("san_username")
        assert username_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in username_field.metadata
        )
        assert has_secret_marker, "san_username should be marked as secret"

        # Check san_password is marked as secret
        password_field = config_class.model_fields.get("san_password")
        assert password_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in password_field.metadata
        )
        assert has_secret_marker, "san_password should be marked as secret"

    def test_dellpowerstore_network_is_secret(self, backend):
        """Test that SAN IP is properly marked as secret."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()
        ip_field = config_class.model_fields.get("san_ip")
        assert ip_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in ip_field.metadata
        )
        assert has_secret_marker, "san_ip should be marked as secret"

    def test_dellpowerstore_config_optional_fields_work(self, backend):
        """Test that optional fields can be omitted."""
        config_class = backend.config_type()

        # Create config with only required fields
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
            }
        )

        # Verify optional fields default to None
        assert config.protocol == "fc"
        assert config.powerstore_nvme is False
        assert config.powerstore_ports is None
        assert config.replication_device is None
        assert config.volume_backend_name is None
        assert config.backend_availability_zone is None


class TestDellpowerstoreConfigValidation:
    """Test Dell PowerStore config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, dellpowerstore_backend):
        """Test that protocol field rejects invalid values."""
        from pydantic import ValidationError

        config_class = dellpowerstore_backend.config_type()

        # Should reject invalid protocol
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-username": "admin",
                    "san-password": "secret",
                    "protocol": "INVALID",
                }
            )

        assert "protocol" in str(exc_info.value).lower()

    def test_boolean_fields_accept_boolean_values(self, dellpowerstore_backend):
        """Test that boolean fields accept boolean values."""
        config_class = dellpowerstore_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
                "powerstore-nvme": True,
            }
        )
        assert config.powerstore_nvme is True


if __name__ == "__main__":
    # This allows running the file directly with pytest
    pytest.main([__file__, "-v"])
