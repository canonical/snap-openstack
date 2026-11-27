# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Dell PowerFlex backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestDellpowerflexBackend(BaseBackendTests):
    """Tests for Dell PowerFlex backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, dellpowerflex_backend):
        """Provide Dell PowerFlex backend instance."""
        return dellpowerflex_backend

    # Backend-specific tests

    def test_backend_type_is_dellpowerflex(self, backend):
        """Test that backend type is 'dellpowerflex'."""
        assert backend.backend_type == "dellpowerflex"

    def test_display_name_mentions_dell(self, backend):
        """Test that display name mentions Dell."""
        assert "dell" in backend.display_name.lower()

    def test_charm_name_is_dellpowerflex_charm(self, backend):
        """Test that charm name is cinder-volume-dellpowerflex."""
        assert backend.charm_name == "cinder-volume-dellpowerflex"

    def test_supports_ha_is_true(self, backend):
        """Test that PowerFlex supports HA."""
        assert backend.supports_ha is True

    def test_dellpowerflex_config_has_required_fields(self, backend):
        """Test that Dell PowerFlex config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        # Verify Dell PowerFlex specific required fields
        required_fields = [
            "san_ip",
            "san_login",
            "san_password",
            "powerflex_storage_pools",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_dellpowerflex_protocol_is_optional_literal(self, backend):
        """Test that protocol field accepts scaleio or nvme-tcp."""
        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None

        # Test config without protocol (optional)
        config_no_protocol = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "powerflex-storage-pools": "domain:pool",
            }
        )
        assert config_no_protocol.protocol == "scaleio"

        # Test valid config with scaleio
        valid_config_scaleio = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "powerflex-storage-pools": "domain:pool",
                "protocol": "scaleio",
            }
        )
        assert valid_config_scaleio.protocol == "scaleio"

        # Test valid config with nvme-tcp
        valid_config_nvme = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "powerflex-storage-pools": "domain:pool",
                "protocol": "nvme-tcp",
            }
        )
        assert valid_config_nvme.protocol == "nvme-tcp"

    def test_dellpowerflex_san_credentials_are_secret(self, backend):
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

    def test_dellpowerflex_replication_device_is_secret(self, backend):
        """Test that replication_device is properly marked as secret."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check replication_device is marked as secret
        replication_field = config_class.model_fields.get("replication_device")
        assert replication_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in replication_field.metadata
        )
        assert has_secret_marker, "replication_device should be marked as secret"

    def test_dellpowerflex_network_is_required(self, backend):
        """Test that SAN IP is a required field."""
        config_class = backend.config_type()
        ip_field = config_class.model_fields.get("san_ip")
        assert ip_field is not None
        assert ip_field.is_required(), "san_ip should be a required field"

    def test_dellpowerflex_storage_pools_is_required(self, backend):
        """Test that storage pools is a required field."""
        config_class = backend.config_type()
        pools_field = config_class.model_fields.get("powerflex_storage_pools")
        assert pools_field is not None
        assert pools_field.is_required(), (
            "powerflex_storage_pools should be a required field"
        )

    def test_dellpowerflex_config_optional_fields_work(self, backend):
        """Test that optional fields can be omitted."""
        config_class = backend.config_type()

        # Create config with only required fields
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "powerflex-storage-pools": "domain:pool",
            }
        )

        # Verify optional fields default to None
        assert config.protocol == "scaleio"
        assert config.powerflex_rest_server_port is None
        assert config.powerflex_max_over_subscription_ratio is None
        assert config.powerflex_round_volume_capacity is None
        assert config.rest_api_connect_timeout is None
        assert config.rest_api_read_timeout is None
        assert config.powerflex_max_image_cache_vtree_size is None
        assert config.replication_device is None
        assert config.driver_ssl_cert is None


class TestDellpowerflexConfigValidation:
    """Test Dell PowerFlex config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, dellpowerflex_backend):
        """Test that protocol field rejects invalid values."""
        config_class = dellpowerflex_backend.config_type()

        # Should reject invalid protocol
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "powerflex-storage-pools": "domain:pool",
                    "protocol": "INVALID",
                }
            )

        assert "protocol" in str(exc_info.value).lower()

    @pytest.mark.parametrize(
        "storage_pools",
        ["domain", "domain:", ":pool", "domain:pool:extra", "domain:pool,"],
    )
    def test_storage_pools_reject_invalid_format(
        self, dellpowerflex_backend, storage_pools
    ):
        """Test that malformed storage pool mappings are rejected."""
        config_class = dellpowerflex_backend.config_type()
        with pytest.raises(ValidationError, match="powerflex-storage-pools"):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "powerflex-storage-pools": storage_pools,
                }
            )

    def test_storage_pools_accept_multiple_mappings(self, dellpowerflex_backend):
        """Test that multiple storage pool mappings are accepted."""
        config = dellpowerflex_backend.config_type().model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "powerflex-storage-pools": "domain1:pool1,domain2:pool2",
            }
        )
        assert config.powerflex_storage_pools == "domain1:pool1,domain2:pool2"

    def test_replication_device_rejects_invalid_format(self, dellpowerflex_backend):
        """Test that malformed replication settings are rejected."""
        with pytest.raises(ValidationError, match="replication-device"):
            dellpowerflex_backend.config_type().model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "powerflex-storage-pools": "domain:pool",
                    "replication-device": "backend_id:wrong,san_ip:192.0.2.1",
                }
            )

    def test_replication_device_accepts_required_settings(self, dellpowerflex_backend):
        """Test that complete replication settings are accepted."""
        replication_device = (
            "backend_id:powerflex_repl,san_ip:192.0.2.1,"
            "san_login:admin,san_password:secret"
        )
        config = dellpowerflex_backend.config_type().model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "powerflex-storage-pools": "domain:pool",
                "replication-device": replication_device,
            }
        )
        assert config.replication_device == replication_device

    def test_boolean_fields_accept_boolean_values(self, dellpowerflex_backend):
        """Test that boolean fields accept boolean values."""
        config_class = dellpowerflex_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "powerflex-storage-pools": "domain:pool",
                "powerflex-round-volume-capacity": True,
            }
        )
        assert config.powerflex_round_volume_capacity is True


if __name__ == "__main__":
    # This allows running the file directly with pytest
    pytest.main([__file__, "-v"])
