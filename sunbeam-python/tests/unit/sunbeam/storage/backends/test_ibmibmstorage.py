# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for IBMStorage backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestIbmibmstorageBackend(BaseBackendTests):
    """Tests for IBMStorage backend."""

    @pytest.fixture
    def backend(self, ibmibmstorage_backend):
        """Provide IBMStorage backend instance."""
        return ibmibmstorage_backend

    def test_backend_type_is_ibmibmstorage(self, backend):
        """Test that backend type is 'ibmibmstorage'."""
        assert backend.backend_type == "ibmibmstorage"

    def test_charm_name_is_ibmibmstorage_charm(self, backend):
        """Test that charm name is cinder-volume-ibmibmstorage."""
        assert backend.charm_name == "cinder-volume-ibmibmstorage"
