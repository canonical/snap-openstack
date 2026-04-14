# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for NetApp backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestNetAppBackend(BaseBackendTests):
    """Tests for NetApp backend."""

    @pytest.fixture
    def backend(self, netapp_backend):
        """Provide NetApp backend instance."""
        return netapp_backend

    def test_backend_type_is_netapp(self, backend):
        """Test that backend type is 'netapp'."""
        assert backend.backend_type == "netapp"

    def test_charm_name_is_netapp_charm(self, backend):
        """Test that charm name is cinder-volume-netapp."""
        assert backend.charm_name == "cinder-volume-netapp"

    def test_config_has_required_fields(self, backend):
        """Test that NetApp config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol", "netapp_ca_certificate_file"):
            assert field in fields, f"Required field {field} not found in config"

    def test_sensitive_fields_are_marked_secret(self, backend):
        """Test that certificate/password fields are marked as secrets."""
        config_class = backend.config_type()
        for field_name in (
            "netapp_password",
            "netapp_private_key_file",
            "netapp_certificate_file",
            "netapp_ca_certificate_file",
        ):
            field = config_class.model_fields.get(field_name)
            assert field is not None
            assert any(isinstance(m, SecretDictField) for m in field.metadata), (
                f"{field_name} should be marked as secret"
            )


class TestNetAppConfigValidation:
    """Test NetApp config validation behavior."""

    def test_protocol_rejects_invalid_value(self, netapp_backend):
        """Test that protocol rejects values other than iscsi/nvme."""
        config_class = netapp_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "fc",
                    "netapp-ca-certificate-file": "ca.pem",
                }
            )

    def test_protocol_accepts_iscsi(self, netapp_backend):
        """Test that protocol accepts iscsi."""
        config_class = netapp_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "iscsi",
                "netapp-ca-certificate-file": "ca.pem",
            }
        )
        assert config.protocol == "iscsi"
