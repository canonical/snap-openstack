# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Inspur InStorage backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestInspurinstorageBackend(BaseBackendTests):
    """Tests for Inspur InStorage backend."""

    @pytest.fixture
    def backend(self, inspurinstorage_backend):
        """Provide Inspur InStorage backend instance."""
        return inspurinstorage_backend

    def test_backend_type_is_inspurinstorage(self, backend):
        """Test that backend type is 'inspurinstorage'."""
        assert backend.backend_type == "inspurinstorage"

    def test_charm_name_is_inspurinstorage_charm(self, backend):
        """Test that charm name is cinder-volume-inspurinstorage."""
        assert backend.charm_name == "cinder-volume-inspurinstorage"
