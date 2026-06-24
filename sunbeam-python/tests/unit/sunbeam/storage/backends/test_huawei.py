# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Huawei OceanStor Dorado backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestHuaweiBackend(BaseBackendTests):
    """Tests for Huawei OceanStor Dorado backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, huawei_backend):
        """Provide Huawei OceanStor Dorado backend instance."""
        return huawei_backend

    # Backend-specific tests

    def test_backend_type_is_huawei(self, backend):
        """Test that backend type is 'huawei'."""
        assert backend.backend_type == "huawei"

    def test_display_name_mentions_huawei(self, backend):
        """Test that display name mentions Huawei."""
        assert "huawei" in backend.display_name.lower()

    def test_charm_name_is_huawei_charm(self, backend):
        """Test that charm name is cinder-volume-huawei."""
        assert backend.charm_name == "cinder-volume-huawei"

    def test_huawei_config_has_required_fields(self, backend):
        """Test that Huawei config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        required_fields = [
            "san_ip",
            "san_login",
            "san_password",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_huawei_san_credentials_are_secret(self, backend):
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

    def test_huawei_metro_san_password_is_secret(self, backend):
        """Test that metro_san_password is properly marked as a secret."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        metro_pass_field = config_class.model_fields.get("metro_san_password")
        assert metro_pass_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in metro_pass_field.metadata
        )
        assert has_secret_marker, "metro_san_password should be marked as secret"

    def test_huawei_san_ip_is_required(self, backend):
        """Test that san_ip is a required field."""
        config_class = backend.config_type()
        ip_field = config_class.model_fields.get("san_ip")
        assert ip_field is not None
        assert ip_field.is_required(), "san_ip should be a required field"

    def test_huawei_protocol_is_optional(self, backend):
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

    def test_huawei_optional_fields_default_to_none(self, backend):
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
        assert config.cinder_huawei_conf_file is None
        assert config.hypermetro_devices is None
        assert config.metro_san_user is None
        assert config.metro_san_password is None
        assert config.metro_domain_name is None
        assert config.metro_san_address is None
        assert config.metro_storage_pools is None
        assert config.volume_backend_name is None
        assert config.backend_availability_zone is None

    def test_huawei_hypermetro_fields_exist(self, backend):
        """Test that HyperMetro replication fields are present in config."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        hypermetro_fields = [
            "hypermetro_devices",
            "metro_san_user",
            "metro_san_password",
            "metro_domain_name",
            "metro_san_address",
            "metro_storage_pools",
        ]
        for field in hypermetro_fields:
            assert field in fields, f"HyperMetro field {field} not found"

    def test_huawei_ha_not_supported(self, backend):
        """Test that Huawei backend does not support HA."""
        assert backend.supports_ha is False

    def test_huawei_charm_base_is_ubuntu(self, backend):
        """Test that charm base is ubuntu@24.04."""
        assert backend.charm_base == "ubuntu@24.04"


class TestHuaweiConfigValidation:
    """Test Huawei OceanStor Dorado config validation behaviour."""

    def test_protocol_rejects_invalid_values(self, huawei_backend):
        """Test that protocol field rejects values other than iscsi/fc."""
        from pydantic import ValidationError

        config_class = huawei_backend.config_type()

        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "nfs",
                }
            )

    def test_missing_required_san_ip_raises(self, huawei_backend):
        """Test that omitting san_ip raises a validation error."""
        from pydantic import ValidationError

        config_class = huawei_backend.config_type()

        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-login": "admin",
                    "san-password": "secret",
                }
            )

    def test_hypermetro_config_fields_accepted(self, huawei_backend):
        """Test that HyperMetro fields are accepted in config."""
        config_class = huawei_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "hypermetro-devices": "device1",
                "metro-san-user": "metro_admin",
                "metro-san-password": "metro_secret",
                "metro-domain-name": "HyperMetroDomain",
                "metro-san-address": "https://192.168.2.1:8080",
                "metro-storage-pools": "pool1,pool2",
            }
        )

        assert config.hypermetro_devices == "device1"
        assert config.metro_san_user == "metro_admin"
        assert config.metro_san_password == "metro_secret"
        assert config.metro_domain_name == "HyperMetroDomain"
        assert config.metro_san_address == "https://192.168.2.1:8080"
        assert config.metro_storage_pools == "pool1,pool2"
