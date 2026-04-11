# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for DataCore storage backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestDatacoreBackend(BaseBackendTests):
    """Tests for DataCore backend."""

    @pytest.fixture
    def backend(self, datacore_backend):
        """Provide DataCore backend instance."""
        return datacore_backend

    def test_backend_type_is_datacore(self, backend):
        """Test that backend type is datacore."""
        assert backend.backend_type == "datacore"

    def test_display_name_mentions_datacore(self, backend):
        """Test that display name mentions DataCore."""
        assert "datacore" in backend.display_name.lower()

    def test_charm_name_is_datacore_charm(self, backend):
        """Test that charm name is cinder-volume-datacore."""
        assert backend.charm_name == "cinder-volume-datacore"

    def test_datacore_config_has_required_contract_fields(self, backend):
        """Test fields required by shared backend contract."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        assert "san_ip" in fields
        assert "protocol" in fields
