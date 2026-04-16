# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Yadro backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestYadroBackend(BaseBackendTests):
    """Tests for Yadro backend."""

    @pytest.fixture
    def backend(self):
        """Provide Yadro backend instance."""
        # This fixture must be injected via pytest's fixture dependency injection
        # If yadro_backend is needed, inject it as a parameter to the test methods instead
        raise NotImplementedError("Use the yadro_backend fixture directly in test methods")

    def test_backend_type_is_yadro(self, backend):
        """Test that backend type is 'yadro'."""
        assert backend.backend_type == "yadro"

    def test_charm_name_is_yadro_charm(self, backend):
        """Test that charm name is cinder-volume-yadro."""
        assert backend.charm_name == "cinder-volume-yadro"

    def test_config_has_required_fields(self, backend):
        """Test that Yadro config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Required field {field} not found in config"


class TestYadroConfigValidation:
    """Test Yadro config validation behavior."""

    def test_protocol_rejects_invalid_value(self, yadro_backend):
        """Test that protocol rejects values other than fc/iscsi."""
        config_class = yadro_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_fc(self, yadro_backend):
        """Test that protocol accepts fc."""
        config_class = yadro_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"
