# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Dell PowerVault backend."""

import pytest

from sunbeam.storage.backends.dellpowervault.backend import Protocol
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestDellPowerVaultBackend(BaseBackendTests):
    """Tests for Dell PowerVault backend."""

    @pytest.fixture
    def backend(self, dellpowervault_backend):
        """Provide Dell PowerVault backend instance."""
        return dellpowervault_backend

    def test_backend_type_is_dellpowervault(self, backend):
        """Test that backend type is 'dellpowervault'."""
        assert backend.backend_type == "dellpowervault"

    def test_display_name_mentions_powervault(self, backend):
        """Test that display name mentions PowerVault."""
        assert "powervault" in backend.display_name.lower()

    def test_charm_name_is_dellpowervault_charm(self, backend):
        """Test that charm name is cinder-volume-dellpowervault."""
        assert backend.charm_name == "cinder-volume-dellpowervault"

    def test_dellpowervault_protocol_uses_enum(self, backend):
        """Test protocol field is typed using the Protocol enum."""
        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None
        assert protocol_field.annotation is Protocol
