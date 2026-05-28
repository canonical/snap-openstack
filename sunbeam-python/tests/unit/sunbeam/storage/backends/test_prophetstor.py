# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for ProphetStor backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestProphetStorBackend(BaseBackendTests):
    """Tests for ProphetStor backend."""

    @pytest.fixture
    def backend(self, prophetstor_backend):
        """Provide ProphetStor backend instance."""
        return prophetstor_backend

    def test_backend_type_is_prophetstor(self, backend):
        """Test that backend type is 'prophetstor'."""
        assert backend.backend_type == "prophetstor"

    def test_charm_name_is_prophetstor_charm(self, backend):
        """Test that charm name is cinder-volume-prophetstor."""
        assert backend.charm_name == "cinder-volume-prophetstor"
