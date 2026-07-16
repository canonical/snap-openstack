# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Dell Unity backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestDellunityBackend(BaseBackendTests):
    """Tests for Dell Unity backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, dellunity_backend):
        """Provide Dell Unity backend instance."""
        return dellunity_backend

    # Backend-specific tests

    def test_backend_type_is_dellunity(self, backend):
        """Test that backend type is 'dellunity'."""
        assert backend.backend_type == "dellunity"

    def test_display_name_mentions_dell(self, backend):
        """Test that display name mentions Dell."""
        assert "dell" in backend.display_name.lower()

    def test_display_name_mentions_unity(self, backend):
        """Test that display name mentions Unity."""
        assert "unity" in backend.display_name.lower()

    def test_charm_name_is_dellunity_charm(self, backend):
        """Test that charm name is cinder-volume-dellunity."""
        assert backend.charm_name == "cinder-volume-dellunity"

    def test_dellunity_config_has_required_fields(self, backend):
        """Test that Dell Unity config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        required_fields = [
            "san_ip",
            "san_login",
            "san_password",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_dellunity_san_credentials_are_secret(self, backend):
        """Test that SAN credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        username_field = config_class.model_fields.get("san_login")
        assert username_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in username_field.metadata
        )
        assert has_secret_marker, "san_login should be marked as secret"

        password_field = config_class.model_fields.get("san_password")
        assert password_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in password_field.metadata
        )
        assert has_secret_marker, "san_password should be marked as secret"

    def test_dellunity_san_ip_is_required(self, backend):
        """Test that san_ip is a required field."""
        config_class = backend.config_type()
        ip_field = config_class.model_fields.get("san_ip")
        assert ip_field is not None
        assert ip_field.is_required(), "san_ip should be a required field"

    def test_dellunity_protocol_is_optional(self, backend):
        """Test that protocol field is optional and accepts iscsi or fc."""
        config_class = backend.config_type()

        # Config without protocol should succeed
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
            }
        )
        assert config.protocol is None

        # Test valid config with iscsi
        config_iscsi = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert config_iscsi.protocol == "iscsi"

        # Test valid config with fc
        config_fc = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert config_fc.protocol == "fc"

    def test_dellunity_optional_fields_default_to_none(self, backend):
        """Test that optional fields default to None when omitted."""
        config_class = backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
            }
        )

        assert config.protocol is None
        assert config.unity_storage_pool_names is None
        assert config.unity_io_ports is None
        assert config.remove_empty_host is None
        assert config.san_thin_provision is None
        assert config.use_multipath_for_image_xfer is None
        assert config.volume_backend_name is None
        assert config.backend_availability_zone is None

    def test_dellunity_unity_specific_fields_exist(self, backend):
        """Test that Dell Unity-specific optional fields are present."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        unity_fields = [
            "unity_storage_pool_names",
            "unity_io_ports",
            "remove_empty_host",
            "san_thin_provision",
            "use_multipath_for_image_xfer",
        ]
        for field in unity_fields:
            assert field in fields, f"Dell Unity field {field} not found"

    def test_dellunity_ha_not_supported(self, backend):
        """Test that Dell Unity backend does not support HA."""
        assert backend.supports_ha is False

    def test_dellunity_charm_base_is_ubuntu(self, backend):
        """Test that charm base is ubuntu@24.04."""
        assert backend.charm_base == "ubuntu@24.04"


class TestDellunityConfigValidation:
    """Test Dell Unity config validation behaviour."""

    def test_protocol_rejects_invalid_values(self, dellunity_backend):
        """Test that protocol field rejects values other than iscsi/fc."""
        from pydantic import ValidationError

        config_class = dellunity_backend.config_type()

        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "nfs",
                }
            )

    def test_missing_required_san_ip_raises(self, dellunity_backend):
        """Test that omitting san_ip raises a validation error."""
        from pydantic import ValidationError

        config_class = dellunity_backend.config_type()

        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-login": "admin",
                    "san-password": "secret",
                }
            )
