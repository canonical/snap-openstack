# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Sandstone backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestSandstoneBackend(BaseBackendTests):
    """Tests for Sandstone backend."""

    @pytest.fixture
    def backend(self, sandstone_backend):
        """Provide Sandstone backend instance."""
        return sandstone_backend

    def test_backend_type_is_sandstone(self, backend):
        """Test that backend type is 'sandstone'."""
        assert backend.backend_type == "sandstone"

    def test_charm_name_is_sandstone_charm(self, backend):
        """Test that charm name is cinder-volume-sandstone."""
        assert backend.charm_name == "cinder-volume-sandstone"
