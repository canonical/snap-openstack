# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for MacroSAN backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestMacrosanBackend(BaseBackendTests):
    """Tests for MacroSAN backend."""

    @pytest.fixture
    def backend(self, macrosan_backend):
        """Provide MacroSAN backend instance."""
        return macrosan_backend

    def test_backend_type_is_macrosan(self, backend):
        """Test that backend type is 'macrosan'."""
        assert backend.backend_type == "macrosan"

    def test_charm_name_is_macrosan_charm(self, backend):
        """Test that charm name is cinder-volume-macrosan."""
        assert backend.charm_name == "cinder-volume-macrosan"
