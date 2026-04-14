# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Open-E backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestOpeneBackend(BaseBackendTests):
    """Tests for Open-E backend."""

    @pytest.fixture
    def backend(self, opene_backend):
        """Provide Open-E backend instance."""
        return opene_backend

    def test_backend_type_is_opene(self, backend):
        """Test that backend type is 'opene'."""
        assert backend.backend_type == "opene"

    def test_charm_name_is_opene_charm(self, backend):
        """Test that charm name is cinder-volume-opene."""
        assert backend.charm_name == "cinder-volume-opene"

    def test_config_has_required_fields(self, backend):
        """Test that Open-E config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol", "chap_password_len"):
            assert field in fields, f"Required field {field} not found in config"

    def test_sensitive_fields_are_marked_secret(self, backend):
        """Test that CHAP password length field is marked as secret."""
        config_class = backend.config_type()
        field = config_class.model_fields.get("chap_password_len")
        assert field is not None
        assert any(isinstance(m, SecretDictField) for m in field.metadata), (
            "chap_password_len should be marked as secret"
        )


class TestOpeneConfigValidation:
    """Test Open-E config validation behavior."""

    def test_protocol_rejects_invalid_value(self, opene_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = opene_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "chap-password-len": "16",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, opene_backend):
        """Test that protocol accepts iscsi."""
        config_class = opene_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "chap-password-len": "16",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
