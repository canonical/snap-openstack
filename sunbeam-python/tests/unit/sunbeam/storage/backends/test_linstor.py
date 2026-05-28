# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for LINSTOR backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestLinstorBackend(BaseBackendTests):
    """Tests for LINSTOR backend."""

    @pytest.fixture
    def backend(self, linstor_backend):
        """Provide LINSTOR backend instance."""
        return linstor_backend

    def test_backend_type_is_linstor(self, backend):
        """Test that backend type is 'linstor'."""
        assert backend.backend_type == "linstor"

    def test_charm_name_is_linstor_charm(self, backend):
        """Test that charm name is cinder-volume-linstor."""
        assert backend.charm_name == "cinder-volume-linstor"
