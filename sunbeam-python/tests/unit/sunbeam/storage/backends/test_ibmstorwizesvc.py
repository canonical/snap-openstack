# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Storwize SVC backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestIbmstorwizesvcBackend(BaseBackendTests):
    """Tests for Storwize SVC backend."""

    @pytest.fixture
    def backend(self, ibmstorwizesvc_backend):
        """Provide Storwize SVC backend instance."""
        return ibmstorwizesvc_backend

    def test_backend_type_is_ibmstorwizesvc(self, backend):
        """Test that backend type is 'ibmstorwizesvc'."""
        assert backend.backend_type == "ibmstorwizesvc"

    def test_charm_name_is_ibmstorwizesvc_charm(self, backend):
        """Test that charm name is cinder-volume-ibmstorwizesvc."""
        assert backend.charm_name == "cinder-volume-ibmstorwizesvc"
