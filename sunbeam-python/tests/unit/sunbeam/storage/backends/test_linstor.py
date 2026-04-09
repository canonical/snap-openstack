# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for LINSTOR backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestLinstorBackend(BaseBackendTests):
    """Tests for LINSTOR backend."""

    @pytest.fixture
    def backend(self, linstor_backend):
        """Provide LINSTOR backend instance."""
        return linstor_backend

    def test_backend_type_is_linstor(self, backend):
        """Test that backend type is 'linstor'."""
        assert backend.backend_type == "linstor"

    def test_charm_name_is_linstor_charm(self, backend):
        """Test that charm name is cinder-volume-linstor."""
        assert backend.charm_name == "cinder-volume-linstor"

    def test_config_has_required_field(self, backend):
        """Test that LINSTOR config exposes its required field."""
        fields = backend.config_type().model_fields
        assert "san_ip" in fields, "Required field san_ip not found in config"


class TestLinstorConfigValidation:
    """Test LINSTOR config validation behavior."""

    def test_protocol_rejects_invalid_value(self, linstor_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = linstor_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, linstor_backend):
        """Test that protocol accepts iscsi."""
        config_class = linstor_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
