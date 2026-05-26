# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Toyou ACS5000 backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestToyouacs5000Backend(BaseBackendTests):
    """Tests for Toyou ACS5000 backend."""

    @pytest.fixture
    def backend(self, toyouacs5000_backend):
        """Provide Toyou ACS5000 backend instance."""
        return toyouacs5000_backend

    def test_backend_type_is_toyouacs5000(self, backend):
        """Test that backend type is 'toyouacs5000'."""
        assert backend.backend_type == "toyouacs5000"

    def test_charm_name_is_toyouacs5000_charm(self, backend):
        """Test that charm name is cinder-volume-toyouacs5000."""
        assert backend.charm_name == "cinder-volume-toyouacs5000"
