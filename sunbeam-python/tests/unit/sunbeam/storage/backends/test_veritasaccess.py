# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Veritas Access backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestVeritasAccessBackend(BaseBackendTests):
    """Tests for Veritas Access backend."""

    @pytest.fixture
    def backend(self, veritasaccess_backend):
        """Provide Veritas Access backend instance."""
        return veritasaccess_backend

    def test_backend_type_is_veritasaccess(self, backend):
        """Test that backend type is 'veritasaccess'."""
        assert backend.backend_type == "veritasaccess"

    def test_charm_name_is_veritasaccess_charm(self, backend):
        """Test that charm name is cinder-volume-veritasaccess."""
        assert backend.charm_name == "cinder-volume-veritasaccess"
