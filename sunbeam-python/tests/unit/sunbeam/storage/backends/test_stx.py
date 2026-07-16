# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Stx backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestStxBackend(BaseBackendTests):
    """Tests for Stx backend."""

    @pytest.fixture
    def backend(self, stx_backend):
        """Provide Stx backend instance."""
        return stx_backend

    def test_backend_type_is_stx(self, backend):
        """Test that backend type is 'stx'."""
        assert backend.backend_type == "stx"

    def test_charm_name_is_stx_charm(self, backend):
        """Test that charm name is cinder-volume-stx."""
        assert backend.charm_name == "cinder-volume-stx"
