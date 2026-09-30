# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for NetApp backend."""

import pytest

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestNetAppBackend(BaseBackendTests):
    """Tests for NetApp backend."""

    @pytest.fixture
    def backend(self, netapp_backend):
        """Provide NetApp backend instance."""
        return netapp_backend

    def test_backend_type_is_netapp(self, backend):
        """Test that backend type is 'netapp'."""
        assert backend.backend_type == "netapp"

    def test_charm_name_is_netapp_charm(self, backend):
        """Test that charm name is cinder-volume-netapp."""
        assert backend.charm_name == "cinder-volume-netapp"

    def test_supports_ha_defaults_false(self, backend):
        """Test that the static default is conservative when config is unknown."""
        assert backend.supports_ha is False

    def test_supports_ha_for_iscsi(self, backend):
        """Test that iSCSI configuration supports HA (active-active driver)."""
        config = backend.config_type().model_validate(
            {"san-ip": "192.168.1.1", "protocol": "iscsi"}
        )
        assert backend.supports_ha_for(config) is True

    def test_supports_ha_for_nvme(self, backend):
        """Test that NVMe configuration does not support HA."""
        config = backend.config_type().model_validate(
            {"san-ip": "192.168.1.1", "protocol": "nvme"}
        )
        assert backend.supports_ha_for(config) is False
