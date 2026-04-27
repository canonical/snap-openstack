# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Kaminario backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestKaminarioBackend(BaseBackendTests):
    """Tests for Kaminario backend."""

    @pytest.fixture
    def backend(self, kaminario_backend):
        """Provide Kaminario backend instance."""
        return kaminario_backend

    def test_backend_type_is_kaminario(self, backend):
        """Test that backend type is 'kaminario'."""
        assert backend.backend_type == "kaminario"

    def test_charm_name_is_kaminario_charm(self, backend):
        """Test that charm name is cinder-volume-kaminario."""
        assert backend.charm_name == "cinder-volume-kaminario"

    def test_config_has_expected_fields(self, backend):
        """Test that Kaminario config exposes expected fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Expected field {field} not found in config"


class TestKaminarioConfigValidation:
    """Test Kaminario config validation behavior."""

    def test_protocol_rejects_invalid_value(self, kaminario_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = kaminario_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, kaminario_backend):
        """Test that protocol accepts iscsi."""
        config_class = kaminario_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
