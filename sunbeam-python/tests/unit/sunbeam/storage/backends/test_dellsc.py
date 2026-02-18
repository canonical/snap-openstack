# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Dell Storage Center backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestDellSCBackend(BaseBackendTests):
    """Tests for Dell Storage Center backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, dellsc_backend):
        """Provide Dell SC backend instance."""
        return dellsc_backend

    # Backend-specific tests

    def test_backend_type_is_dellsc(self, backend):
        """Test that backend type is 'dellsc'."""
        assert backend.backend_type == "dellsc"

    def test_display_name_mentions_dell(self, backend):
        """Test that display name mentions Dell."""
        assert "dell" in backend.display_name.lower()

    def test_charm_name_is_dellsc_charm(self, backend):
        """Test that charm name is cinder-volume-dellsc."""
        assert backend.charm_name == "cinder-volume-dellsc"

    def test_dellsc_config_has_required_fields(self, backend):
        """Test that Dell SC config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        # Verify Dell SC-specific required fields
        required_fields = [
            "san_ip",
            "san_login",
            "san_password",
            "dell_sc_ssn",
            "protocol",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_dellsc_protocol_is_required_literal(self, backend):
        """Test that protocol field is required and accepts fc or iscsi."""
        from pydantic import ValidationError

        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None

        # Test config without protocol (required)
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "dell-sc-ssn": 12345,
                }
            )

        # Test valid config with fc
        valid_config_fc = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "dell-sc-ssn": 12345,
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
                "dell-sc-ssn": 12345,
                "protocol": "iscsi",
            }
        )
        assert valid_config_iscsi.protocol == "iscsi"

    def test_dellsc_san_credentials_are_secret(self, backend):
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

    def test_dellsc_secondary_credentials_are_secret(self, backend):
        """Test that secondary SAN credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check secondary_san_login is marked as secret
        sec_user_field = config_class.model_fields.get("secondary_san_login")
        assert sec_user_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in sec_user_field.metadata
        )
        assert has_secret_marker, "secondary_san_login should be marked as secret"

        # Check secondary_san_password is marked as secret
        sec_pass_field = config_class.model_fields.get("secondary_san_password")
        assert sec_pass_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in sec_pass_field.metadata
        )
        assert has_secret_marker, "secondary_san_password should be marked as secret"

    def test_dellsc_config_optional_fields_work(self, backend):
        """Test that optional fields can be omitted."""
        config_class = backend.config_type()

        # Create config with only required fields
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "dell-sc-ssn": 12345,
                "protocol": "fc",
            }
        )

        # Verify optional fields default to None
        assert config.volume_backend_name is None
        assert config.backend_availability_zone is None
        assert config.dell_sc_api_port is None

    def test_dellsc_dell_specific_fields_exist(self, backend):
        """Test that Dell SC-specific fields exist."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        dell_specific_fields = [
            "dell_sc_ssn",
            "dell_sc_api_port",
            "dell_sc_server_folder",
            "dell_sc_volume_folder",
            "dell_server_os",
            "dell_sc_verify_cert",
        ]
        for field in dell_specific_fields:
            assert field in fields, f"Dell SC field {field} not found"

    def test_dellsc_dual_dsm_fields_exist(self, backend):
        """Test that dual DSM configuration fields exist."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        dual_dsm_fields = [
            "secondary_san_ip",
            "secondary_san_login",
            "secondary_san_password",
            "secondary_sc_api_port",
        ]
        for field in dual_dsm_fields:
            assert field in fields, f"Dual DSM field {field} not found"

    def test_dellsc_network_filtering_fields_exist(self, backend):
        """Test that network filtering fields exist."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        network_fields = [
            "excluded_domain_ips",
            "excluded_domain_ip",
            "included_domain_ips",
        ]
        for field in network_fields:
            assert field in fields, f"Network filtering field {field} not found"

    def test_dellsc_ssh_fields_exist(self, backend):
        """Test that SSH configuration fields exist."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        ssh_fields = [
            "ssh_conn_timeout",
            "ssh_max_pool_conn",
            "ssh_min_pool_conn",
        ]
        for field in ssh_fields:
            assert field in fields, f"SSH field {field} not found"

    def test_dellsc_api_timeout_fields_exist(self, backend):
        """Test that API timeout fields exist."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        timeout_fields = [
            "dell_api_async_rest_timeout",
            "dell_api_sync_rest_timeout",
        ]
        for field in timeout_fields:
            assert field in fields, f"API timeout field {field} not found"


class TestDellSCConfigValidation:
    """Test Dell SC config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, dellsc_backend):
        """Test that protocol field rejects invalid values."""
        from pydantic import ValidationError

        config_class = dellsc_backend.config_type()

        # Should reject invalid protocol
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "dell-sc-ssn": 12345,
                "protocol": "INVALID",
            }
        )

        assert "protocol" in str(exc_info.value).lower()

    def test_boolean_fields_accept_boolean_values(self, dellsc_backend):
        """Test that boolean fields accept boolean values."""
        config_class = dellsc_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "dell-sc-ssn": 12345,
                "protocol": "fc",
                "san-thin-provision": True,
                "dell-sc-verify-cert": False,
            }
        )
        assert config.san_thin_provision is True
        assert config.dell_sc_verify_cert is False

    def test_enable_unsupported_driver_must_be_true(self, dellsc_backend):
        """Test that enable-unsupported-driver cannot be set to false."""
        from pydantic import ValidationError

        config_class = dellsc_backend.config_type()

        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "dell-sc-ssn": 12345,
                    "protocol": "fc",
                    "enable-unsupported-driver": False,
                }
            )

        errors = exc_info.value.errors()
        assert errors
        assert errors[0]["loc"] == ("enable-unsupported-driver",)

    def test_integer_fields_accept_integer_values(self, dellsc_backend):
        """Test that integer fields accept integer values."""
        config_class = dellsc_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
                "dell-sc-ssn": 12345,
                "dell-sc-api-port": 3033,
                "secondary-sc-api-port": 3033,
                "dell-api-async-rest-timeout": 30,
                "dell-api-sync-rest-timeout": 60,
            }
        )
        assert config.dell_sc_ssn == 12345
        assert config.dell_sc_api_port == 3033
        assert config.secondary_sc_api_port == 3033
        assert config.dell_api_async_rest_timeout == 30
        assert config.dell_api_sync_rest_timeout == 60

    def test_ssh_pool_connection_values(self, dellsc_backend):
        """Test that SSH pool connection values are accepted."""
        config_class = dellsc_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "dell-sc-ssn": 12345,
                "protocol": "fc",
                "ssh-conn-timeout": 30,
                "ssh-max-pool-conn": 5,
                "ssh-min-pool-conn": 1,
            }
        )
        assert config.ssh_conn_timeout == 30
        assert config.ssh_max_pool_conn == 5
        assert config.ssh_min_pool_conn == 1

    def test_dual_dsm_configuration(self, dellsc_backend):
        """Test that dual DSM configuration works together."""
        config_class = dellsc_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "dell-sc-ssn": 12345,
                "protocol": "fc",
                "secondary-san-ip": "192.168.1.2",
                "secondary-san-login": "admin2",
                "secondary-san-password": "secret2",
                "secondary-sc-api-port": 3034,
            }
        )
        assert config.secondary_san_ip == "192.168.1.2"
        assert config.secondary_san_login == "admin2"
        assert config.secondary_san_password == "secret2"
        assert config.secondary_sc_api_port == 3034


if __name__ == "__main__":
    # This allows running the file directly with pytest
    pytest.main([__file__, "-v"])
