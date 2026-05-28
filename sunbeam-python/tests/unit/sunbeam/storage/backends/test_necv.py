# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for NEC V backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestNecvBackend(BaseBackendTests):
    """Tests for NEC V backend."""

    @pytest.fixture
    def backend(self, necv_backend):
        """Provide NEC V backend instance."""
        return necv_backend

    def test_backend_type_is_necv(self, backend):
        """Test that backend type is 'necv'."""
        assert backend.backend_type == "necv"

    def test_charm_name_is_necv_charm(self, backend):
        """Test that charm name is cinder-volume-necv."""
        assert backend.charm_name == "cinder-volume-necv"
