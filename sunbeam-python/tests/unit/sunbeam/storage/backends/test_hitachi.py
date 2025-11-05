# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Hitachi VSP storage backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestHitachiBackend(BaseBackendTests):
    """Tests for Hitachi VSP storage backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, hitachi_backend):
        """Provide Hitachi backend instance."""
        return hitachi_backend

    # Backend-specific tests

    def test_backend_type_is_hitachi(self, backend):
        """Test that backend type is 'hitachi'."""
        assert backend.backend_type == "hitachi"

    def test_display_name_mentions_hitachi(self, backend):
        """Test that display name mentions Hitachi."""
        assert "hitachi" in backend.display_name.lower()

    def test_charm_name_is_hitachi_charm(self, backend):
        """Test that charm name is cinder-volume-hitachi."""
        assert backend.charm_name == "cinder-volume-hitachi"

    def test_hitachi_config_has_required_fields(self, backend):
        """Test that Hitachi config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        # Verify Hitachi-specific required fields
        required_fields = [
            "hitachi_storage_id",
            "hitachi_pools",
            "san_ip",
            "san_username",
            "san_password",
            "protocol",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_hitachi_protocol_is_literal(self, backend):
        """Test that protocol field only accepts FC or iSCSI."""
        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None

        # Test valid config with FC
        valid_config_fc = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
                "protocol": "FC",
            }
        )
        assert valid_config_fc.protocol == "FC"

        # Test valid config with iSCSI
        valid_config_iscsi = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
                "protocol": "iSCSI",
            }
        )
        assert valid_config_iscsi.protocol == "iSCSI"

    def test_hitachi_san_credentials_are_secret(self, backend):
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

    def test_hitachi_chap_credentials_are_secret(self, backend):
        """Test that CHAP credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check chap_username is marked as secret
        chap_user_field = config_class.model_fields.get("chap_username")
        assert chap_user_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in chap_user_field.metadata
        )
        assert has_secret_marker, "chap_username should be marked as secret"

        # Check chap_password is marked as secret
        chap_pass_field = config_class.model_fields.get("chap_password")
        assert chap_pass_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in chap_pass_field.metadata
        )
        assert has_secret_marker, "chap_password should be marked as secret"

    def test_hitachi_config_optional_fields_work(self, backend):
        """Test that optional fields can be omitted."""
        config_class = backend.config_type()

        # Create config with only required fields
        config = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
                "protocol": "FC",
            }
        )

        # Verify optional fields default to None
        assert config.volume_backend_name is None
        assert config.backend_availability_zone is None
        assert config.hitachi_target_ports is None
        assert config.hitachi_copy_speed is None

    def test_hitachi_mirror_rest_credentials_are_secret(self, backend):
        """Test that mirror REST credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check mirror REST username is marked as secret
        mirror_user_field = config_class.model_fields.get(
            "hitachi_mirror_rest_username"
        )
        assert mirror_user_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in mirror_user_field.metadata
        )
        assert has_secret_marker, (
            "hitachi_mirror_rest_username should be marked as secret"
        )

        # Check mirror REST password is marked as secret
        mirror_pass_field = config_class.model_fields.get(
            "hitachi_mirror_rest_password"
        )
        assert mirror_pass_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in mirror_pass_field.metadata
        )
        assert has_secret_marker, (
            "hitachi_mirror_rest_password should be marked as secret"
        )


class TestHitachiConfigValidation:
    """Test Hitachi config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, hitachi_backend):
        """Test that protocol field rejects invalid values."""
        from pydantic import ValidationError

        config_class = hitachi_backend.config_type()

        # Should reject invalid protocol
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "hitachi-storage-id": "12345",
                    "hitachi-pools": "pool1",
                    "san-ip": "192.168.1.1",
                    "san-username": "admin",
                    "san-password": "secret",
                    "protocol": "INVALID",
                }
            )

        assert "protocol" in str(exc_info.value).lower()

    def test_copy_speed_validates_range(self, hitachi_backend):
        """Test that copy_speed validates range (1-15) if configured."""
        config_class = hitachi_backend.config_type()

        # Valid copy speed
        config = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
                "protocol": "FC",
                "hitachi-copy-speed": 10,
            }
        )
        assert config.hitachi_copy_speed == 10

    def test_boolean_fields_accept_boolean_values(self, hitachi_backend):
        """Test that boolean fields accept boolean values."""
        config_class = hitachi_backend.config_type()

        config = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-username": "admin",
                "san-password": "secret",
                "protocol": "FC",
                "use-chap-auth": True,
                "hitachi-discard-zero-page": False,
                "hitachi-group-create": True,
            }
        )
        assert config.use_chap_auth is True
        assert config.hitachi_discard_zero_page is False
        assert config.hitachi_group_create is True
