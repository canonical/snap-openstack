# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Synology backend."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestSynologyBackend(BaseBackendTests):
    """Tests for Synology backend."""

    @pytest.fixture
    def backend(self, synology_backend):
        """Provide Synology backend instance."""
        return synology_backend

    def test_backend_type_is_synology(self, backend):
        """Test that backend type is 'synology'."""
        assert backend.backend_type == "synology"

    def test_charm_name_is_synology_charm(self, backend):
        """Test that charm name is cinder-volume-synology."""
        assert backend.charm_name == "cinder-volume-synology"

    def test_display_name_uses_synology_branding(self, backend):
        """Test that display name consistently uses Synology."""
        assert backend.display_name == "Synology iSCSI"

    def test_config_has_required_fields(self, backend):
        """Test that Synology config has required fields."""
        fields = backend.config_type().model_fields
        for field in (
            "san_ip",
            "protocol",
            "synology_password",
            "synology_one_time_pass",
        ):
            assert field in fields, f"Required field {field} not found in config"

    def test_sensitive_fields_are_marked_secret(self, backend):
        """Test Synology secret fields are marked as secret."""
        config_class = backend.config_type()
        for field_name in ("synology_password", "synology_one_time_pass"):
            field = config_class.model_fields.get(field_name)
            assert field is not None
            assert any(isinstance(m, SecretDictField) for m in field.metadata), (
                f"{field_name} should be marked as secret"
            )


class TestSynologyConfigValidation:
    """Test Synology config validation behavior."""

    def test_protocol_rejects_invalid_value(self, synology_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = synology_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "synology-password": "secret",
                    "synology-one-time-pass": "otp",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, synology_backend):
        """Test that protocol accepts iscsi."""
        config_class = synology_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "synology-password": "secret",
                "synology-one-time-pass": "otp",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"

    def test_one_time_password_is_optional(self, synology_backend):
        """Test that one-time password is optional when OTP is not enabled."""
        config_class = synology_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "synology-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert config.synology_one_time_pass is None

    @pytest.mark.parametrize(
        "field_name,field_value",
        [
            ("synology-admin-port", None),
            ("synology-username", None),
            ("synology-ssl-verify", None),
        ],
    )
    def test_optional_defaults_reject_none(
        self, synology_backend, field_name, field_value
    ):
        """Test that non-nullable fields with defaults reject explicit None."""
        config_class = synology_backend.config_type()
        payload = {
            "san-ip": "192.168.1.1",
            "synology-password": "secret",
            "protocol": "iscsi",
            field_name: field_value,
        }
        with pytest.raises(ValidationError):
            config_class.model_validate(payload)
