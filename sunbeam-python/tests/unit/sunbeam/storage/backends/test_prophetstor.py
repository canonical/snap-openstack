# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for ProphetStor backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestProphetstorBackend(BaseBackendTests):
    """Tests for ProphetStor backend."""

    @pytest.fixture
    def backend(self, prophetstor_backend):
        """Provide ProphetStor backend instance."""
        return prophetstor_backend

    def test_backend_type_is_prophetstor(self, backend):
        """Test that backend type is 'prophetstor'."""
        assert backend.backend_type == "prophetstor"

    def test_charm_name_is_prophetstor_charm(self, backend):
        """Test that charm name is cinder-volume-prophetstor."""
        assert backend.charm_name == "cinder-volume-prophetstor"

    def test_config_has_required_fields(self, backend):
        """Test that ProphetStor config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Required field {field} not found in config"


class TestProphetstorConfigValidation:
    """Test ProphetStor config validation behavior."""

    def test_protocol_rejects_invalid_value(self, prophetstor_backend):
        """Test that protocol rejects values other than fc/iscsi."""
        config_class = prophetstor_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_fc(self, prophetstor_backend):
        """Test that protocol accepts fc."""
        config_class = prophetstor_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"
