# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Veritas Access backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestVeritasaccessBackend(BaseBackendTests):
    """Tests for Veritas Access backend."""

    @pytest.fixture
    def backend(self, veritasaccess_backend):
        """Provide Veritas Access backend instance."""
        return veritasaccess_backend

    def test_backend_type_is_veritasaccess(self, backend):
        """Test that backend type is 'veritasaccess'."""
        assert backend.backend_type == "veritasaccess"

    def test_charm_name_is_veritasaccess_charm(self, backend):
        """Test that charm name is cinder-volume-veritasaccess."""
        assert backend.charm_name == "cinder-volume-veritasaccess"

    def test_config_has_required_fields(self, backend):
        """Test that Veritas Access config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Required field {field} not found in config"


class TestVeritasaccessConfigValidation:
    """Test Veritas Access config validation behavior."""

    def test_protocol_rejects_invalid_value(self, veritasaccess_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = veritasaccess_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, veritasaccess_backend):
        """Test that protocol accepts iscsi."""
        config_class = veritasaccess_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
