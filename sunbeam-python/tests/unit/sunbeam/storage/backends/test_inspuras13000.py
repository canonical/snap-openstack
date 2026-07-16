# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Inspur AS13000 backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestInspuras13000Backend(BaseBackendTests):
    """Tests for Inspur AS13000 backend."""

    @pytest.fixture
    def backend(self, inspuras13000_backend):
        """Provide Inspur AS13000 backend instance."""
        return inspuras13000_backend

    def test_backend_type_is_inspuras13000(self, backend):
        """Test that backend type is 'inspuras13000'."""
        assert backend.backend_type == "inspuras13000"

    def test_charm_name_is_inspuras13000_charm(self, backend):
        """Test that charm name is cinder-volume-inspuras13000."""
        assert backend.charm_name == "cinder-volume-inspuras13000"
