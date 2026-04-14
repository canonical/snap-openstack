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

    def test_config_has_stx_specific_fields(self, backend):
        """Test that Stx config exposes Stx-specific fields."""
        fields = backend.config_type().model_fields
        for field in ("seagate_pool_name", "seagate_pool_type", "seagate_iscsi_ips"):
            assert field in fields, f"Stx-specific field {field} not found in config"

    def test_seagate_pool_name_has_expected_default(self, backend):
        """Test that seagate_pool_name default is set."""
        field = backend.config_type().model_fields["seagate_pool_name"]
        assert field.default == "A"


class TestStxConfigValidation:
    """Test Stx config validation behavior."""

    def test_protocol_rejects_invalid_value(self, stx_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = stx_backend.config_type()
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "fc",
                }
            )
        assert any(
            error.get("loc") == ("protocol",) for error in exc_info.value.errors()
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

    def test_pool_type_accepts_valid_value(self, stx_backend):
        """Test that seagate_pool_type accepts valid enum values."""
        config_class = stx_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "seagate-pool-type": "linear",
            }
        )
        assert str(config.seagate_pool_type) == "linear"

    def test_pool_type_rejects_invalid_value(self, stx_backend):
        """Test that seagate_pool_type rejects invalid values."""
        config_class = stx_backend.config_type()
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "seagate-pool-type": "raid",
                }
            )
        assert any(
            error.get("loc") == ("seagate-pool-type",)
            for error in exc_info.value.errors()
        )
