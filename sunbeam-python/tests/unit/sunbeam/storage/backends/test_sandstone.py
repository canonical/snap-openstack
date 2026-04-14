# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Sandstone backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestSandstoneBackend(BaseBackendTests):
    """Tests for Sandstone backend."""

    @pytest.fixture
    def backend(self, sandstone_backend):
        """Provide Sandstone backend instance."""
        return sandstone_backend

    def test_backend_type_is_sandstone(self, backend):
        """Test that backend type is 'sandstone'."""
        assert backend.backend_type == "sandstone"

    def test_charm_name_is_sandstone_charm(self, backend):
        """Test that charm name is cinder-volume-sandstone."""
        assert backend.charm_name == "cinder-volume-sandstone"

    def test_config_has_required_fields(self, backend):
        """Test that Sandstone config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Required field {field} not found in config"


class TestSandstoneConfigValidation:
    """Test Sandstone config validation behavior."""

    def test_protocol_rejects_invalid_value(self, sandstone_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = sandstone_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, sandstone_backend):
        """Test that protocol accepts iscsi."""
        config_class = sandstone_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
