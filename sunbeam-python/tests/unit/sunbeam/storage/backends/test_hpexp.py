# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for HPE XP backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestHpexpBackend(BaseBackendTests):
    """Tests for HPE XP backend."""

    @pytest.fixture
    def backend(self, hpexp_backend):
        """Provide HPE XP backend instance."""
        return hpexp_backend

    def test_backend_type_is_hpexp(self, backend):
        """Test that backend type is 'hpexp'."""
        assert backend.backend_type == "hpexp"

    def test_charm_name_is_hpexp_charm(self, backend):
        """Test that charm name is cinder-volume-hpexp."""
        assert backend.charm_name == "cinder-volume-hpexp"

    def test_hpexp_config_has_required_fields(self, backend):
        """Test that HPE XP config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Required field {field} not found in config"


class TestHpexpConfigValidation:
    """Test HPE XP config validation behavior."""

    def test_protocol_rejects_invalid_value(self, hpexp_backend):
        """Test that protocol rejects values other than fc/iscsi."""
        config_class = hpexp_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "nvme",
                }
            )

    def test_protocol_accepts_fc(self, hpexp_backend):
        """Test that protocol accepts fc."""
        config_class = hpexp_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "fc",
            }
        )
        assert config.protocol == "fc"
