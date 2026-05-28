# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for IBM FlashSystem iSCSI backend."""

import pytest
from pydantic import ValidationError

from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestIbmflashsystemiscsiBackend(BaseBackendTests):
    """Tests for IBM FlashSystem iSCSI backend."""

    @pytest.fixture
    def backend(self, ibmflashsystemiscsi_backend):
        """Provide IBM FlashSystem iSCSI backend instance."""
        return ibmflashsystemiscsi_backend

    def test_backend_type_is_ibmflashsystemiscsi(self, backend):
        """Test that backend type is 'ibmflashsystemiscsi'."""
        assert backend.backend_type == "ibmflashsystemiscsi"

    def test_charm_name_is_ibmflashsystemiscsi_charm(self, backend):
        """Test that charm name is cinder-volume-ibmflashsystemiscsi."""
        assert backend.charm_name == "cinder-volume-ibmflashsystemiscsi"

    def test_config_has_expected_fields(self, backend):
        """Test that IBM FlashSystem iSCSI config exposes expected fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol"):
            assert field in fields, f"Expected field {field} not found in config"


class TestIbmflashsystemiscsiConfigValidation:
    """Test IBM FlashSystem iSCSI config validation behavior."""

    def test_san_ip_is_required(self, ibmflashsystemiscsi_backend):
        """Test that san-ip is required."""
        config_class = ibmflashsystemiscsi_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "protocol": "iscsi",
                }
            )

    def test_protocol_rejects_invalid_value(self, ibmflashsystemiscsi_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = ibmflashsystemiscsi_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, ibmflashsystemiscsi_backend):
        """Test that protocol accepts iscsi."""
        config_class = ibmflashsystemiscsi_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"
