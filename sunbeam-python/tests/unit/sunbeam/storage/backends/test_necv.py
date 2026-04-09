# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for NEC V backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestNecvBackend(BaseBackendTests):
    """Tests for NEC V backend."""

    @pytest.fixture
    def backend(self, necv_backend):
        """Provide NEC V backend instance."""
        return necv_backend

    def test_backend_type_is_necv(self, backend):
        """Test that backend type is 'necv'."""
        assert backend.backend_type == "necv"

    def test_charm_name_is_necv_charm(self, backend):
        """Test that charm name is cinder-volume-necv."""
        assert backend.charm_name == "cinder-volume-necv"

    def test_config_has_required_fields(self, backend):
        """Test that NEC V config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Required field {field} not found in config"


class TestNecvConfigValidation:
    """Test NEC V config validation behavior."""

    def test_protocol_rejects_invalid_value(self, necv_backend):
        """Test that protocol rejects values other than fc/iscsi."""
        config_class = necv_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_fc(self, necv_backend):
        """Test that protocol accepts fc."""
        config_class = necv_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"
