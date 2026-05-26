# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Yadro backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestYadroBackend(BaseBackendTests):
    """Tests for Yadro backend."""

    @pytest.fixture
    def backend(self, yadro_backend):
        """Provide Yadro backend instance."""
        return yadro_backend

    def test_backend_type_is_yadro(self, backend):
        """Test that backend type is 'yadro'."""
        assert backend.backend_type == "yadro"

    def test_charm_name_is_yadro_charm(self, backend):
        """Test that charm name is cinder-volume-yadro."""
        assert backend.charm_name == "cinder-volume-yadro"
