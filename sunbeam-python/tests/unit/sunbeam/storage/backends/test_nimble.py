# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for HPE Nimble storage backend."""

from typing import get_args, get_origin

import pytest

from sunbeam.storage.backends.nimble.backend import Protocol
from sunbeam.storage.models import SecretDictField
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestNimbleBackend(BaseBackendTests):
    """Tests for HPE Nimble backend.

    Inherits all generic tests from BaseBackendTests and adds backend-specific tests.
    """

    @pytest.fixture
    def backend(self, nimble_backend):
        """Provide Nimble backend instance."""
        return nimble_backend

    def test_backend_type_is_nimble(self, backend):
        """Test that backend type is 'nimble'."""
        assert backend.backend_type == "nimble"

    def test_display_name_mentions_nimble(self, backend):
        """Test that display name mentions Nimble."""
        assert "nimble" in backend.display_name.lower()

    def test_charm_name_is_nimble_charm(self, backend):
        """Test that charm name is cinder-volume-nimble."""
        assert backend.charm_name == "cinder-volume-nimble"

    def test_nimble_config_has_required_fields(self, backend):
        """Test that Nimble config has all required fields."""
        fields = backend.config_type().model_fields
        required_fields = ["san_ip", "san_login", "san_password"]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_nimble_protocol_uses_protocol_enum(self, backend):
        """Test that protocol field uses Protocol enum as source of truth."""
        protocol_field = backend.config_type().model_fields["protocol"]
        annotation = protocol_field.annotation
        assert get_origin(annotation) is not None
        assert Protocol in get_args(annotation)

    def test_nimble_san_credentials_are_secret(self, backend):
        """Test that SAN credentials are properly marked as secrets."""
        fields = backend.config_type().model_fields
        for field_name in ("san_login", "san_password"):
            field = fields.get(field_name)
            assert field is not None
            has_secret_marker = any(
                isinstance(meta, SecretDictField) for meta in field.metadata
            )
            assert has_secret_marker, f"{field_name} should be marked as secret"


class TestNimbleConfigValidation:
    """Test Nimble config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, nimble_backend):
        """Test that protocol field accepts only iscsi or fc."""
        from pydantic import ValidationError

        config_class = nimble_backend.config_type()

        valid_config_iscsi = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert valid_config_iscsi.protocol == "iscsi"

        valid_config_fc = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert valid_config_fc.protocol == "fc"

        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "invalid",
                }
            )

        assert "protocol" in str(exc_info.value).lower()
