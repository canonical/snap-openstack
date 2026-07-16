# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Zadara backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestZadaraBackend(BaseBackendTests):
    """Tests for Zadara backend."""

    @pytest.fixture
    def backend(self, zadara_backend):
        """Provide Zadara backend instance."""
        return zadara_backend

    def test_backend_type_is_zadara(self, backend):
        """Test that backend type is 'zadara'."""
        assert backend.backend_type == "zadara"

    def test_charm_name_is_zadara_charm(self, backend):
        """Test that charm name is cinder-volume-zadara."""
        assert backend.charm_name == "cinder-volume-zadara"
