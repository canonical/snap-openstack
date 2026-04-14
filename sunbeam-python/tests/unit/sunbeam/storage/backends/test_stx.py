# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Stx backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestStxBackend(BaseBackendTests):
    """Tests for Stx backend."""

    @pytest.fixture
    def backend(self, stx_backend):
        """Provide Stx backend instance."""
        return stx_backend

    def test_backend_type_is_stx(self, backend):
        """Test that backend type is 'stx'."""
        assert backend.backend_type == "stx"

    def test_charm_name_is_stx_charm(self, backend):
        """Test that charm name is cinder-volume-stx."""
        assert backend.charm_name == "cinder-volume-stx"

    def test_config_has_required_fields(self, backend):
        """Test that Stx config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Required field {field} not found in config"


class TestStxConfigValidation:
    """Test Stx config validation behavior."""

    def test_protocol_rejects_invalid_value(self, stx_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = stx_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, stx_backend):
        """Test that protocol accepts iscsi."""
        config_class = stx_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
