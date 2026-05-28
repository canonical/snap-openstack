# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for QNAP backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestQnapBackend(BaseBackendTests):
    """Tests for QNAP backend."""

    @pytest.fixture
    def backend(self, qnap_backend):
        """Provide QNAP backend instance."""
        return qnap_backend

    def test_backend_type_is_qnap(self, backend):
        """Test that backend type is 'qnap'."""
        assert backend.backend_type == "qnap"

    def test_charm_name_is_qnap_charm(self, backend):
        """Test that charm name is cinder-volume-qnap."""
        assert backend.charm_name == "cinder-volume-qnap"
