# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for HPE 3Par backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestHpethreeparBackend(BaseBackendTests):
    """Tests for HPE 3Par backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, hpe3par_backend):
        """Provide HPE 3Par backend instance."""
        return hpe3par_backend

    # Backend-specific tests

    def test_backend_type_is_hpethreepar(self, backend):
        """Test that backend type is 'hpe3par'."""
        assert backend.backend_type == "hpe3par"

    def test_display_name_mentions_hpe(self, backend):
        """Test that display name mentions HPE."""
        assert "hpe" in backend.display_name.lower()

    def test_charm_name_is_hpe3par_charm(self, backend):
        """Test that charm name is cinder-volume-hpe3par."""
        assert backend.charm_name == "cinder-volume-hpe3par"

    def test_hpe3par_config_has_required_fields(self, backend):
        """Test that HPE 3Par config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        # Verify HPE 3Par specific required fields
        required_fields = [
            "san_ip",
            "san_login",
            "san_password",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_hpe3par_protocol_is_optional_literal(self, backend):
        """Test that protocol field accepts fc or iscsi."""
        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None

        # Test config without protocol (optional)
        config_no_protocol = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
            }
        )
        assert config_no_protocol.protocol == "fc"

        # Test valid config with fc
        valid_config_fc = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert valid_config_fc.protocol == "fc"

        # Test valid config with iscsi
        valid_config_iscsi = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert valid_config_iscsi.protocol == "iscsi"

    def test_hpe3par_san_credentials_are_secret(self, backend):
        """Test that SAN credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check san_login is marked as secret
        username_field = config_class.model_fields.get("san_login")
        assert username_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in username_field.metadata
        )
        assert has_secret_marker, "san_login should be marked as secret"

        # Check san_password is marked as secret
        password_field = config_class.model_fields.get("san_password")
        assert password_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in password_field.metadata
        )
        assert has_secret_marker, "san_password should be marked as secret"

        # Check hpe3par_username is marked as secret
        hpe3par_username = config_class.model_fields.get("hpe3par_username")
        assert hpe3par_username is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in hpe3par_username.metadata
        )
        assert has_secret_marker, "hpe3par_username should be marked as secret"

        # Check hpe3par_password is marked as secret
        hpe3par_password = config_class.model_fields.get("hpe3par_password")
        assert hpe3par_password is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in hpe3par_password.metadata
        )
        assert has_secret_marker, "hpe3par_password should be marked as secret"

    def test_hpe3par_config_optional_fields_work(self, backend):
        """Test that optional fields can be omitted."""
        config_class = backend.config_type()

        # Create config with only required fields
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "hpe3par-api-url": "http://192.168.1.1/api/v1",
            }
        )

        # Verify optional fields default to None
        assert config.protocol == "fc"
        assert config.hpe3par_debug is False
        assert config.hpe3par_api_url == "http://192.168.1.1/api/v1"
        assert config.hpe3par_target_nsp is None
        assert config.hpe3par_iscsi_ips is None
        assert config.hpe3par_iscsi_chap_enabled is False
        assert config.replication_device is None
        assert config.volume_backend_name is None
        assert config.backend_availability_zone is None


class TestHpethreeparConfigValidation:
    """Test HPE 3Par config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, hpe3par_backend):
        """Test that protocol field rejects invalid values."""
        from pydantic import ValidationError

        config_class = hpe3par_backend.config_type()

        # Should reject invalid protocol
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "INVALID",
                }
            )

        assert "protocol" in str(exc_info.value).lower()

    def test_boolean_fields_accept_boolean_values(self, hpe3par_backend):
        """Test that boolean fields accept boolean values."""
        config_class = hpe3par_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "hpe3par-debug": True,
                "use-multipath-for-image-xfer": True,
                "enforce-multipath-for-image-xfer": True,
            }
        )
        assert config.hpe3par_debug is True
        assert config.use_multipath_for_image_xfer is True
        assert config.enforce_multipath_for_image_xfer is True

    def test_numeric_fields_accept_numeric_values(self, hpe3par_backend):
        """Test that numeric fields accept numeric values."""
        config_class = hpe3par_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "reserved-percentage": 30,
                "max-over-subscription-ratio": 0.5,
            }
        )
        assert config.reserved_percentage == 30
        assert config.max_over_subscription_ratio == 0.5

    def test_hpe3par_fields_serialize_to_kebab_case(self, hpe3par_backend):
        """Test that hpe3par_* fields serialize to correct kebab-case keys."""
        config_class = hpe3par_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "hpe3par-api-url": "http://192.168.1.1/api/v1",
                "hpe3par-debug": True,
                "hpe3par-iscsi-chap-enabled": True,
                "hpe3par-cpg": "cpg1",
                "hpe3par-target-nsp": "1:2:3",
                "hpe3par-iscsi-ips": "10.0.0.1",
            }
        )

        serialized = config.model_dump(by_alias=True)

        assert serialized["hpe3par-api-url"] == "http://192.168.1.1/api/v1"
        assert serialized["hpe3par-debug"] is True
        assert serialized["hpe3par-iscsi-chap-enabled"] is True
        assert serialized["hpe3par-cpg"] == "cpg1"
        assert serialized["hpe3par-target-nsp"] == "1:2:3"
        assert serialized["hpe3par-iscsi-ips"] == "10.0.0.1"

        # Ensure broken alias_generator output is NOT present
        assert "hpe-3par-debug" not in serialized
        assert "hpe-3par-api-url" not in serialized

    def test_broken_hpe_3par_keys_are_ignored_during_validation(self, hpe3par_backend):
        """Test that hpe-3par-* keys (broken alias_generator form) are ignored."""
        config_class = hpe3par_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "hpe-3par-debug": True,
                "hpe-3par-api-url": "http://192.168.1.1/api/v1",
                "hpe-3par-iscsi-chap-enabled": True,
            }
        )

        # Fields should remain at their defaults since the keys are not recognised
        assert config.hpe3par_debug is False
        assert config.hpe3par_api_url is None
        assert config.hpe3par_iscsi_chap_enabled is False


if __name__ == "__main__":
    # This allows running the file directly with pytest
    pytest.main([__file__, "-v"])
