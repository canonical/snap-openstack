# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Open-E backend."""

import pytest
from pydantic import ValidationError

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

    def test_config_has_required_fields(self, backend):
        """Test that Open-E config has required fields."""
        fields = backend.config_type().model_fields
        for field in ("san_ip", "protocol", "chap_password_len"):
            assert field in fields, f"Required field {field} not found in config"

    def test_chap_password_len_is_numeric(self, backend):
        """Test that CHAP password length field is configured as int."""
        config_class = backend.config_type()
        field = config_class.model_fields.get("chap_password_len")
        assert field is not None
        assert field.annotation is int


class TestOpeneConfigValidation:
    """Test Open-E config validation behavior."""

    def test_protocol_rejects_invalid_value(self, opene_backend):
        """Test that protocol rejects values other than iscsi."""
        config_class = opene_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "chap-password-len": 16,
                    "protocol": "fc",
                }
            )

    def test_protocol_accepts_iscsi(self, opene_backend):
        """Test that protocol accepts iscsi."""
        config_class = opene_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "chap-password-len": 16,
                "protocol": "iscsi",
            }
        )
        assert config.protocol == "iscsi"

    def test_jovian_block_size_accepts_valid_value(self, opene_backend):
        """Test that jovian_block_size accepts valid enum values."""
        config_class = opene_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "chap-password-len": 16,
                "protocol": "iscsi",
                "jovian-block-size": "16K",
            }
        )
        assert str(config.jovian_block_size) == "16K"

    def test_jovian_block_size_rejects_invalid_value(self, opene_backend):
        """Test that jovian_block_size rejects invalid values."""
        config_class = opene_backend.config_type()
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "chap-password-len": 16,
                    "protocol": "iscsi",
                    "jovian-block-size": "8K",
                }
            )

    def test_optional_fields_round_trip_with_kebab_aliases(self, opene_backend):
        """Test optional fields are parsed/serialized via kebab-case aliases."""
        config_class = opene_backend.config_type()
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "chap-password-len": 16,
                "protocol": "iscsi",
                "san-hosts": "10.0.0.10",
                "jovian-recovery-delay": 20,
                "jovian-ignore-tpath": "10.0.0.11,10.0.0.12",
                "jovian-pool": "pool-1",
                "jovian-block-size": "32K",
            }
        )
        dumped = config.model_dump(by_alias=True)
        assert dumped["san-hosts"] == "10.0.0.10"
        assert dumped["jovian-recovery-delay"] == 20
        assert dumped["jovian-ignore-tpath"] == "10.0.0.11,10.0.0.12"
        assert dumped["jovian-pool"] == "pool-1"
        assert dumped["jovian-block-size"] == "32K"
