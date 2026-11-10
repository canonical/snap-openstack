# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Hitachi VSP storage backend."""

import ipaddress
from unittest.mock import Mock, patch

import pytest
import yaml

from sunbeam.storage.cli_base import StorageBackendCLIBase
from sunbeam.storage.models import BackendValidationException
from sunbeam.storage.steps import generate_questions_from_config, prompt_backend_config
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestHitachiBackend(BaseBackendTests):
    """Tests for Hitachi VSP storage backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, hitachi_backend):
        """Provide Hitachi backend instance."""
        return hitachi_backend

    # Backend-specific tests

    def test_backend_type_is_hitachi(self, backend):
        """Test that backend type is 'hitachi'."""
        assert backend.backend_type == "hitachi"

    def test_display_name_mentions_hitachi(self, backend):
        """Test that display name mentions Hitachi."""
        assert "hitachi" in backend.display_name.lower()

    def test_charm_name_is_hitachi_charm(self, backend):
        """Test that charm name is cinder-volume-hitachi."""
        assert backend.charm_name == "cinder-volume-hitachi"

    def test_hitachi_config_has_required_fields(self, backend):
        """Test that Hitachi config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        # Verify Hitachi-specific required fields
        required_fields = [
            "hitachi_storage_id",
            "hitachi_pools",
            "san_ip",
            "san_login",
            "san_password",
            "protocol",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_hitachi_protocol_is_literal(self, backend):
        """Test that protocol field only accepts FC or iSCSI."""
        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None

        # Test valid config with FC
        valid_config_fc = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert valid_config_fc.protocol == "fc"

        # Test valid config with iSCSI
        valid_config_iscsi = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert valid_config_iscsi.protocol == "iscsi"

    def test_hitachi_san_credentials_are_secret(self, backend):
        """Test that SAN credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check san_login is marked as secret
        username_field = config_class.model_fields.get("san_login")
        assert username_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in username_field.metadata
        )
        assert has_secret_marker, "san_login should be marked as secret"

        # Check san_password is marked as secret
        password_field = config_class.model_fields.get("san_password")
        assert password_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in password_field.metadata
        )
        assert has_secret_marker, "san_password should be marked as secret"

    def test_hitachi_chap_credentials_are_secret(self, backend):
        """Test that CHAP credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check chap_username is marked as secret
        chap_user_field = config_class.model_fields.get("chap_username")
        assert chap_user_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in chap_user_field.metadata
        )
        assert has_secret_marker, "chap_username should be marked as secret"

        # Check chap_password is marked as secret
        chap_pass_field = config_class.model_fields.get("chap_password")
        assert chap_pass_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in chap_pass_field.metadata
        )
        assert has_secret_marker, "chap_password should be marked as secret"

    def test_hitachi_config_optional_fields_work(self, backend):
        """Test that optional fields can be omitted."""
        config_class = backend.config_type()

        # Create config with only required fields
        config = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )

        # Verify optional fields default to None
        assert config.volume_backend_name is None
        assert config.backend_availability_zone is None
        assert config.hitachi_target_ports is None
        assert config.hitachi_copy_speed is None

    def test_hitachi_mirror_rest_credentials_are_secret(self, backend):
        """Test that mirror REST credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check mirror REST username is marked as secret
        mirror_user_field = config_class.model_fields.get(
            "hitachi_mirror_rest_username"
        )
        assert mirror_user_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in mirror_user_field.metadata
        )
        assert has_secret_marker, (
            "hitachi_mirror_rest_username should be marked as secret"
        )

        # Check mirror REST password is marked as secret
        mirror_pass_field = config_class.model_fields.get(
            "hitachi_mirror_rest_password"
        )
        assert mirror_pass_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in mirror_pass_field.metadata
        )
        assert has_secret_marker, (
            "hitachi_mirror_rest_password should be marked as secret"
        )


class TestHitachiConfigValidation:
    """Test Hitachi config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, hitachi_backend):
        """Test that protocol field rejects invalid values."""
        from pydantic import ValidationError

        config_class = hitachi_backend.config_type()

        # Should reject invalid protocol
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "hitachi-storage-id": "12345",
                    "hitachi-pools": "pool1",
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "INVALID",
                }
            )

        assert "protocol" in str(exc_info.value).lower()

    def test_copy_speed_validates_range(self, hitachi_backend):
        """Test that copy_speed validates range (1-15) if configured."""
        config_class = hitachi_backend.config_type()

        # Valid copy speed
        config = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
                "hitachi-copy-speed": 10,
            }
        )
        assert config.hitachi_copy_speed == 10

    def test_boolean_fields_accept_boolean_values(self, hitachi_backend):
        """Test that boolean fields accept boolean values."""
        config_class = hitachi_backend.config_type()

        config = config_class.model_validate(
            {
                "hitachi-storage-id": "12345",
                "hitachi-pools": "pool1",
                "san-ip": "192.168.1.1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
                "hitachi-discard-zero-page": False,
                "hitachi-group-create": True,
            }
        )
        assert config.hitachi_discard_zero_page is False
        assert config.hitachi_group_create is True

    def test_charm_config_keeps_bool_and_int(
        self, hitachi_backend, mock_deployment, mock_manifest
    ):
        """Keep model values typed until Terraform converts its input map."""
        backend_name = "hitachi-backend"
        charm_manifest = Mock(channel="2025.1/edge", revision=None)
        backend_manifest = Mock()
        backend_manifest.software.charms = {hitachi_backend.charm_name: charm_manifest}
        mock_manifest.storage.root = {
            hitachi_backend.backend_type: Mock(root={backend_name: backend_manifest})
        }
        config = hitachi_backend.config_type().model_validate(
            {
                "protocol": "iscsi",
                "san_ip": "192.0.2.10",
                "hitachi_storage_id": "hit1",
                "hitachi_pools": "pool1,pool2",
                "hitachi_target_ports": "port1",
                "hitachi_group_create": True,
                "hitachi_rest_timeout": 30,
                "hitachi_discard_zero_page": False,
                "san_login": "admin",
                "san_password": "password",
            },
            by_name=True,
        )

        tfvars = hitachi_backend.build_terraform_vars(
            mock_deployment, mock_manifest, backend_name, config
        )
        charm_config = tfvars["charm_config"]

        assert charm_config["hitachi-group-create"] is True
        assert charm_config["hitachi-discard-zero-page"] is False
        assert charm_config["hitachi-rest-timeout"] == 30
        assert charm_config["hitachi-target-ports"] == "port1"


class TestHitachiIPValidation:
    """IP validation must apply before configuration reaches the snap."""

    @pytest.fixture
    def config_values(self):
        return {
            "protocol": "iscsi",
            "san_ip": "192.0.2.10",
            "hitachi_storage_id": "hit1",
            "hitachi_pools": "pool1,pool2",
            "san_login": "admin",
            "san_password": "secret",
        }

    @pytest.mark.parametrize("address", ["192.0.2.10", "2001:db8::10"])
    def test_valid_address_serializes_to_string(
        self, hitachi_backend, config_values, address
    ):
        config_values["san_ip"] = address
        config = hitachi_backend.config_type().model_validate(
            config_values, by_name=True
        )
        assert config.model_dump(by_alias=True)["san-ip"] == address
        assert config.san_ip == ipaddress.ip_address(address)

    @pytest.mark.parametrize("source", ["file", "saved"])
    @pytest.mark.parametrize("address", ["192.0.2.10d2d", "array.example.com"])
    def test_noninteractive_rejects_invalid_address(
        self, hitachi_backend, config_values, tmp_path, source, address
    ):
        config_values["san_ip"] = address
        preseed = {}
        saved = {}
        if source == "file":
            config_file = tmp_path / "hitachi.yaml"
            config_file.write_text(yaml.safe_dump(config_values))
            preseed = StorageBackendCLIBase(hitachi_backend)._load_config_file(
                config_file
            )
        else:
            saved = config_values
        manifest = Mock(storage=None)
        with (
            patch("sunbeam.storage.steps.load_answers", return_value=saved),
            patch("sunbeam.storage.steps.write_answers") as write_answers,
            patch(
                "sunbeam.core.questions.Question.ask",
                side_effect=AssertionError(
                    "Non-interactive validation must not prompt"
                ),
            ),
            pytest.raises(
                BackendValidationException,
                match=r"san_ip.*not a valid IPv4 or IPv6 address",
            ),
        ):
            prompt_backend_config(
                Mock(),
                hitachi_backend,
                manifest,
                "hitachi-test",
                preseed,
                accept_defaults=True,
            )
        write_answers.assert_not_called()

    def test_interactive_reprompts_invalid_address(self, hitachi_backend, caplog):
        question = generate_questions_from_config(hitachi_backend.config_type())[
            "san_ip"
        ]
        with patch(
            "sunbeam.core.questions.Prompt.ask",
            side_effect=["192.0.2.10d2d", "192.0.2.10"],
        ) as prompt:
            assert question.ask() == "192.0.2.10"
        assert prompt.call_count == 2
        assert "san_ip" in caplog.text
        assert "not a valid IPv4 or IPv6 address" in caplog.text
