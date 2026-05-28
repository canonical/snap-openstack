# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Kaminario backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestKaminarioBackend(BaseBackendTests):
    """Tests for Kaminario backend."""

    @pytest.fixture
    def backend(self, kaminario_backend):
        """Provide Kaminario backend instance."""
        return kaminario_backend

    def test_backend_type_is_kaminario(self, backend):
        """Test that backend type is 'kaminario'."""
        assert backend.backend_type == "kaminario"

    def test_charm_name_is_kaminario_charm(self, backend):
        """Test that charm name is cinder-volume-kaminario."""
        assert backend.charm_name == "cinder-volume-kaminario"
