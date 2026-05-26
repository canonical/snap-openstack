# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Nexenta backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestNexentaBackend(BaseBackendTests):
    """Tests for Nexenta backend."""

    @pytest.fixture
    def backend(self, nexenta_backend):
        """Provide Nexenta backend instance."""
        return nexenta_backend

    def test_backend_type_is_nexenta(self, backend):
        """Test that backend type is 'nexenta'."""
        assert backend.backend_type == "nexenta"

    def test_charm_name_is_nexenta_charm(self, backend):
        """Test that charm name is cinder-volume-nexenta."""
        assert backend.charm_name == "cinder-volume-nexenta"
