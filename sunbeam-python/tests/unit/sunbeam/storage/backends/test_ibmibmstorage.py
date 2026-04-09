# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for IBMStorage backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestIbmibmstorageBackend(BaseBackendTests):
    """Tests for IBMStorage backend."""

    @pytest.fixture
    def backend(self, ibmibmstorage_backend):
        """Provide IBMStorage backend instance."""
        return ibmibmstorage_backend

    def test_backend_type_is_ibmibmstorage(self, backend):
        """Test that backend type is 'ibmibmstorage'."""
        assert backend.backend_type == "ibmibmstorage"

    def test_charm_name_is_ibmibmstorage_charm(self, backend):
        """Test that charm name is cinder-volume-ibmibmstorage."""
        assert backend.charm_name == "cinder-volume-ibmibmstorage"

    def test_config_has_expected_fields(self, backend):
        """Test that IBMStorage config exposes expected fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "san_login", "san_password", "protocol"):
            assert field in fields, f"Expected field {field} not found in config"

    def test_san_credentials_are_secret(self, backend):
        """Test that SAN login and password are marked as secrets."""
        config_class = backend.config_type()
        for field_name in ("san_login", "san_password"):
            field = config_class.model_fields.get(field_name)
            assert field is not None
            assert any(isinstance(m, SecretDictField) for m in field.metadata), (
                f"{field_name} should be marked as secret"
            )


class TestIbmibmstorageConfigValidation:
    """Test IBMStorage config validation behavior."""

    def test_protocol_rejects_invalid_value(self, ibmibmstorage_backend):
        """Test that protocol rejects values other than fc/iscsi."""
        config_class = ibmibmstorage_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_fc(self, ibmibmstorage_backend):
        """Test that protocol accepts fc."""
        config_class = ibmibmstorage_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"

    def test_connection_type_accepts_fibre_channel(self, ibmibmstorage_backend):
        """Test that connection_type accepts fibre_channel."""
        config_class = ibmibmstorage_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "connection-type": "fibre_channel",
            }
        )
        assert config.connection_type == "fibre_channel"

    def test_connection_type_rejects_invalid_value(self, ibmibmstorage_backend):
        """Test that connection_type rejects invalid values."""
        config_class = ibmibmstorage_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "connection-type": "fc",
                }
            )

    def test_chap_accepts_enabled(self, ibmibmstorage_backend):
        """Test that chap accepts enabled."""
        config_class = ibmibmstorage_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "chap": "enabled",
            }
        )
        assert config.chap == "enabled"

    def test_chap_rejects_invalid_value(self, ibmibmstorage_backend):
        """Test that chap rejects invalid values."""
        config_class = ibmibmstorage_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "chap": "auto",
                }
            )

    def test_protocol_and_connection_type_must_match(self, ibmibmstorage_backend):
        """Test that protocol and connection_type must be consistent."""
        config_class = ibmibmstorage_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "fc",
                    "connection-type": "iscsi",
                }
            )
