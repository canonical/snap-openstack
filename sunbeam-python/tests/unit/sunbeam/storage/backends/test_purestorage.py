# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Pure Storage FlashArray backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestPureStorageBackend(BaseBackendTests):
    """Tests for Pure Storage FlashArray backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, purestorage_backend):
        """Provide Pure Storage backend instance."""
        return purestorage_backend

    # Backend-specific tests

    def test_backend_type_is_purestorage(self, backend):
        """Test that backend type is 'purestorage'."""
        assert backend.backend_type == "purestorage"

    def test_display_name_mentions_pure(self, backend):
        """Test that display name mentions Pure Storage."""
        assert "pure" in backend.display_name.lower()

    def test_charm_name_is_purestorage_charm(self, backend):
        """Test that charm name is cinder-volume-purestorage."""
        assert backend.charm_name == "cinder-volume-purestorage"

    def test_purestorage_config_has_required_fields(self, backend):
        """Test that Pure Storage config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        # Verify Pure Storage-specific required fields
        required_fields = [
            "san_ip",
            "pure_api_token",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_purestorage_protocol_is_optional_literal(self, backend):
        """Test that protocol field accepts iscsi, fc, or nvme."""
        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None

        # Test config without protocol (optional)
        config_no_protocol = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
            }
        )
        assert config_no_protocol.protocol is None

        # Test valid config with iscsi
        valid_config_iscsi = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
                "protocol": "iscsi",
            }
        )
        assert valid_config_iscsi.protocol == "iscsi"

        # Test valid config with fc
        valid_config_fc = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
                "protocol": "fc",
            }
        )
        assert valid_config_fc.protocol == "fc"

        # Test valid config with nvme
        valid_config_nvme = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
                "protocol": "nvme",
            }
        )
        assert valid_config_nvme.protocol == "nvme"

    def test_purestorage_api_token_is_secret(self, backend):
        """Test that API token is properly marked as secret."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check pure_api_token is marked as secret
        token_field = config_class.model_fields.get("pure_api_token")
        assert token_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in token_field.metadata
        )
        assert has_secret_marker, "pure_api_token should be marked as secret"

    def test_purestorage_config_optional_fields_work(self, backend):
        """Test that optional fields can be omitted."""
        config_class = backend.config_type()

        # Create config with only required fields
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
            }
        )

        # Verify optional fields default to None
        assert config.protocol is None
        assert config.pure_iscsi_cidr is None
        assert config.pure_nvme_cidr is None
        assert config.pure_host_personality is None
        assert config.pure_eradicate_on_delete is None

    def test_purestorage_personality_enum(self, backend):
        """Test that host personality accepts valid enum values."""
        config_class = backend.config_type()

        # Test with valid personality
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
                "pure-host-personality": "esxi",
            }
        )
        assert config.pure_host_personality == "esxi"

    def test_purestorage_replication_fields_exist(self, backend):
        """Test that replication-related fields exist."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        replication_fields = [
            "pure_replica_interval_default",
            "pure_replica_retention_short_term_default",
            "pure_replication_pg_name",
            "pure_replication_pod_name",
            "pure_trisync_enabled",
        ]
        for field in replication_fields:
            assert field in fields, f"Replication field {field} not found"

    def test_purestorage_iscsi_fields_exist(self, backend):
        """Test that iSCSI-related fields exist."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        iscsi_fields = [
            "pure_iscsi_cidr",
            "pure_iscsi_cidr_list",
        ]
        for field in iscsi_fields:
            assert field in fields, f"iSCSI field {field} not found"

    def test_purestorage_nvme_fields_exist(self, backend):
        """Test that NVMe-related fields exist."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        nvme_fields = [
            "pure_nvme_cidr",
            "pure_nvme_cidr_list",
            "pure_nvme_transport",
        ]
        for field in nvme_fields:
            assert field in fields, f"NVMe field {field} not found"


class TestPureStorageConfigValidation:
    """Test Pure Storage config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, purestorage_backend):
        """Test that protocol field rejects invalid values."""
        from pydantic import ValidationError

        config_class = purestorage_backend.config_type()

        # Should reject invalid protocol
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "pure-api-token": "secret-token",
                    "protocol": "INVALID",
                }
            )

        assert "protocol" in str(exc_info.value).lower()

    def test_nvme_transport_accepts_only_tcp(self, purestorage_backend):
        """Test that NVMe transport only accepts tcp."""
        from pydantic import ValidationError

        config_class = purestorage_backend.config_type()

        # Valid transport
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
                "pure-nvme-transport": "tcp",
            }
        )
        assert config.pure_nvme_transport == "tcp"

        # Should reject invalid transport
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "pure-api-token": "secret-token",
                    "pure-nvme-transport": "roce",  # Not supported yet
                }
            )

        assert (
            "pure_nvme_transport" in str(exc_info.value).lower()
            or "nvme" in str(exc_info.value).lower()
        )

    def test_boolean_fields_accept_boolean_values(self, purestorage_backend):
        """Test that boolean fields accept boolean values."""
        config_class = purestorage_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
                "pure-automatic-max-oversubscription-ratio": True,
                "pure-eradicate-on-delete": False,
                "pure-trisync-enabled": True,
            }
        )
        assert config.pure_automatic_max_oversubscription_ratio is True
        assert config.pure_eradicate_on_delete is False
        assert config.pure_trisync_enabled is True

    def test_integer_fields_accept_integer_values(self, purestorage_backend):
        """Test that integer fields accept integer values."""
        config_class = purestorage_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "pure-api-token": "secret-token",
                "pure-replica-interval-default": 3600,
                "pure-replica-retention-short-term-default": 86400,
                "pure-replica-retention-long-term-per-day-default": 3,
                "pure-replica-retention-long-term-default": 7,
            }
        )
        assert config.pure_replica_interval_default == 3600
        assert config.pure_replica_retention_short_term_default == 86400
        assert config.pure_replica_retention_long_term_per_day_default == 3
        assert config.pure_replica_retention_long_term_default == 7
