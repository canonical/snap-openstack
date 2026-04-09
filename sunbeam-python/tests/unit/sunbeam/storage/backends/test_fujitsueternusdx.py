# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Fujitsu ETERNUS DX backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestFujitsueternusdxBackend(BaseBackendTests):
    """Tests for Fujitsu ETERNUS DX backend."""

    @pytest.fixture
    def backend(self, fujitsueternusdx_backend):
        """Provide Fujitsu ETERNUS DX backend instance."""
        return fujitsueternusdx_backend

    def test_backend_type_is_fujitsueternusdx(self, backend):
        """Test that backend type is 'fujitsueternusdx'."""
        assert backend.backend_type == "fujitsueternusdx"

    def test_charm_name_is_fujitsueternusdx_charm(self, backend):
        """Test that charm name is cinder-volume-fujitsueternusdx."""
        assert backend.charm_name == "cinder-volume-fujitsueternusdx"

    def test_fujitsueternusdx_config_has_required_fields(self, backend):
        """Test that Fujitsu ETERNUS DX config has all required fields."""
        fields = backend.config_type().model_fields
        required_fields = ["san_ip", "fujitsu_passwordless", "protocol"]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_fujitsu_passwordless_is_boolean_toggle(self, backend):
        """Test that fujitsu_passwordless is a boolean toggle."""
        config_class = backend.config_type()
        field = config_class.model_fields.get("fujitsu_passwordless")
        assert field is not None
        assert field.annotation is bool


class TestFujitsueternusdxConfigValidation:
    """Test Fujitsu ETERNUS DX config validation behavior."""

    def test_protocol_rejects_invalid_value(self, fujitsueternusdx_backend):
        """Test that protocol rejects values other than fc."""
        config_class = fujitsueternusdx_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "fujitsu-passwordless": True,
                    "protocol": "nvme",
                }
            )

        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "fujitsu-passwordless": True,
                    "protocol": "iscsi",
                }
            )

    def test_protocol_accepts_fc(self, fujitsueternusdx_backend):
        """Test that protocol accepts fc."""
        config_class = fujitsueternusdx_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "fujitsu-passwordless": True,
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"
