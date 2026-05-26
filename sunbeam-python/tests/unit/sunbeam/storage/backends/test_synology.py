# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Synology backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestSynologyBackend(BaseBackendTests):
    """Tests for Synology backend."""

    @pytest.fixture
    def backend(self, synology_backend):
        """Provide Synology backend instance."""
        return synology_backend

    def test_backend_type_is_synology(self, backend):
        """Test that backend type is 'synology'."""
        assert backend.backend_type == "synology"

    def test_charm_name_is_synology_charm(self, backend):
        """Test that charm name is cinder-volume-synology."""
        assert backend.charm_name == "cinder-volume-synology"
