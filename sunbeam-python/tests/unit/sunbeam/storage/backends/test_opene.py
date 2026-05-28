# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Open-E backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestOpeneBackend(BaseBackendTests):
    """Tests for Open-E backend."""

    @pytest.fixture
    def backend(self, opene_backend):
        """Provide Open-E backend instance."""
        return opene_backend

    def test_backend_type_is_opene(self, backend):
        """Test that backend type is 'opene'."""
        assert backend.backend_type == "opene"

    def test_charm_name_is_opene_charm(self, backend):
        """Test that charm name is cinder-volume-opene."""
        assert backend.charm_name == "cinder-volume-opene"
