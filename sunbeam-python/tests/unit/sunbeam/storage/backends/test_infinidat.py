# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Infinidat storage backend."""

from unittest.mock import MagicMock

import pytest

from sunbeam.core.manifest import (
    CharmManifest,
    Manifest,
    SoftwareConfig,
    StorageBackendManifests,
    StorageInstanceManifest,
    StorageManifest,
)
from sunbeam.storage.backends.infinidat.backend import InfinidatConfig
from tests.unit.sunbeam.storage.backends.test_common import BaseBackendTests


class TestInfinidatBackend(BaseBackendTests):
    """Tests for Infinidat storage backend.

    Inherits all generic tests from BaseBackendTests and adds
    backend-specific tests.
    """

    @pytest.fixture
    def backend(self, infinidat_backend):
        """Provide Infinidat backend instance."""
        return infinidat_backend

    # Backend-specific tests

    def test_backend_type_is_infinidat(self, backend):
        """Test that backend type is 'infinidat'."""
        assert backend.backend_type == "infinidat"

    def test_display_name_mentions_infinidat(self, backend):
        """Test that display name mentions Infinidat."""
        assert "infinidat" in backend.display_name.lower()

    def test_charm_name_is_infinidat_charm(self, backend):
        """Test that charm name is cinder-volume-infinidat."""
        assert backend.charm_name == "cinder-volume-infinidat"

    def test_infinidat_config_has_required_fields(self, backend):
        """Test that Infinidat config has all required fields."""
        config_class = backend.config_type()
        fields = config_class.model_fields

        # Verify Infinidat-specific required fields
        required_fields = [
            "san_ip",
            "infinidat_pool_name",
            "san_login",
            "san_password",
        ]
        for field in required_fields:
            assert field in fields, f"Required field {field} not found in config"

    def test_infinidat_protocol_is_optional_literal(self, backend):
        """Test that protocol field accepts iscsi or fc."""
        config_class = backend.config_type()
        protocol_field = config_class.model_fields.get("protocol")
        assert protocol_field is not None

        # Test valid config with iSCSI (default)
        valid_config_iscsi = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "iscsi",
            }
        )
        assert valid_config_iscsi.protocol == "iscsi"

        # Test valid config with FC
        valid_config_fc = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
                "protocol": "fc",
            }
        )
        assert valid_config_fc.protocol == "fc"

    def test_infinidat_protocol_defaults_to_iscsi(self, backend):
        """Test that protocol defaults to iscsi when not specified."""
        config_class = backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
            }
        )
        assert config.protocol == "iscsi"

    def test_infinidat_san_credentials_are_secret(self, backend):
        """Test that SAN credentials are properly marked as secrets."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()

        # Check san_login is marked as secret
        login_field = config_class.model_fields.get("san_login")
        assert login_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in login_field.metadata
        )
        assert has_secret_marker, "san_login should be marked as secret"

        # Check san_password is marked as secret
        password_field = config_class.model_fields.get("san_password")
        assert password_field is not None
        has_secret_marker = any(
            isinstance(m, SecretDictField) for m in password_field.metadata
        )
        assert has_secret_marker, "san_password should be marked as secret"

    def test_infinidat_chap_credentials_are_secret(self, backend):
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

    def test_infinidat_config_optional_fields_work(self, backend):
        """Test that optional fields can be omitted."""
        config_class = backend.config_type()

        # Create config with only required fields
        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
            }
        )

        # Verify optional fields default to expected values
        assert config.protocol == "iscsi"  # defaults to iscsi
        assert config.infinidat_iscsi_netspaces is None
        assert config.use_chap_auth is None
        assert config.chap_username is None
        assert config.chap_password is None
        assert config.volume_backend_name is None
        assert config.backend_availability_zone is None

    def test_infinidat_use_chap_auth_defaults_to_none(self, backend):
        """Test that use_chap_auth defaults to None (defers to charm)."""
        config_class = backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
            }
        )
        assert config.use_chap_auth is None

    def test_infinidat_supports_ha(self, backend):
        """Test that Infinidat backend supports HA deployments."""
        assert backend.supports_ha is True

    def test_infinidat_principal_application_is_ha(self, backend):
        """Test that principal application is cinder-volume (HA)."""
        assert backend.principal_application == "cinder-volume"

    def test_infinidat_new_optional_fields_default_to_none(self, backend):
        """Test that new optional fields default to None."""
        config_class = backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
            }
        )

        assert config.infinidat_use_compression is None
        assert config.max_over_subscription_ratio is None


class TestInfinidatConfigValidation:
    """Test Infinidat config validation behavior."""

    def test_protocol_accepts_only_valid_values(self, infinidat_backend):
        """Test that protocol field rejects invalid values."""
        from pydantic import ValidationError

        config_class = infinidat_backend.config_type()

        # Should reject invalid protocol
        with pytest.raises(ValidationError) as exc_info:
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "infinidat-pool-name": "pool1",
                    "san-login": "admin",
                    "san-password": "secret",
                    "protocol": "INVALID",
                }
            )

        assert "protocol" in str(exc_info.value).lower()

    def test_missing_required_fields_raises_error(self, infinidat_backend):
        """Test that missing required fields raise ValidationError."""
        from pydantic import ValidationError

        config_class = infinidat_backend.config_type()

        # Missing san_ip
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "infinidat-pool-name": "pool1",
                    "san-login": "admin",
                    "san-password": "secret",
                }
            )

        # Missing infinidat_pool_name
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "san-login": "admin",
                    "san-password": "secret",
                }
            )

        # Missing san_login
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "infinidat-pool-name": "pool1",
                    "san-password": "secret",
                }
            )

        # Missing san_password
        with pytest.raises(ValidationError):
            config_class.model_validate(
                {
                    "san-ip": "192.168.1.1",
                    "infinidat-pool-name": "pool1",
                    "san-login": "admin",
                }
            )

    def test_boolean_fields_accept_boolean_values(self, infinidat_backend):
        """Test that boolean fields accept boolean values."""
        config_class = infinidat_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
                "use-chap-auth": False,
            }
        )
        assert config.use_chap_auth is False

    def test_max_over_subscription_ratio_accepts_float(self, infinidat_backend):
        """Test that max_over_subscription_ratio accepts float values."""
        config_class = infinidat_backend.config_type()

        config = config_class.model_validate(
            {
                "san-ip": "192.168.1.1",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
                "max-over-subscription-ratio": 20.0,
            }
        )
        assert config.max_over_subscription_ratio == 20.0


class TestInfinidatBuildTerraformVars:
    """Tests for the Infinidat build_terraform_vars override.

    Verifies the local charm source support via manifest model_extra.
    """

    @pytest.fixture
    def mock_deployment(self):
        """Provide a mock Deployment object."""
        deployment = MagicMock()
        deployment.get_space.return_value = "alpha"
        return deployment

    @pytest.fixture
    def sample_config(self):
        """Provide a sample InfinidatConfig instance."""
        return InfinidatConfig.model_validate(
            {
                "san-ip": "192.168.1.100",
                "infinidat-pool-name": "pool1",
                "san-login": "admin",
                "san-password": "secret",
            }
        )

    @pytest.fixture
    def empty_manifest(self):
        """Provide a Manifest with no storage overrides."""
        return Manifest(storage=StorageManifest(root={}))

    def _make_manifest(self, channel=None, revision=None):
        """Build a Manifest with optional charm config for the infinidat backend.

        Args:
            channel: optional channel override
            revision: optional revision override
        """
        charm_kwargs = {}
        if channel is not None:
            charm_kwargs["channel"] = channel
        if revision is not None:
            charm_kwargs["revision"] = revision

        charm_manifest = CharmManifest(**charm_kwargs)

        backend_instance = StorageInstanceManifest(
            software=SoftwareConfig(charms={"cinder-volume-infinidat": charm_manifest})
        )
        backend_manifests = StorageBackendManifests(
            root={"my-backend": backend_instance}
        )
        return Manifest(storage=StorageManifest(root={"infinidat": backend_manifests}))

    def test_no_manifest_override_uses_defaults(
        self, infinidat_backend, mock_deployment, sample_config, empty_manifest
    ):
        """With no storage manifest entries, defaults from the class are used."""
        result = infinidat_backend.build_terraform_vars(
            mock_deployment, empty_manifest, "my-backend", sample_config
        )

        assert result["charm_name"] == "cinder-volume-infinidat"
        assert result["charm_channel"] == "latest/edge"
        assert result["charm_revision"] is None
        assert result["charm_base"] == "ubuntu@24.04"

    def test_channel_and_revision_override(
        self, infinidat_backend, mock_deployment, sample_config
    ):
        """When both channel and revision are set in manifest, both are applied."""
        manifest = self._make_manifest(channel="stable", revision=42)
        result = infinidat_backend.build_terraform_vars(
            mock_deployment, manifest, "my-backend", sample_config
        )

        assert result["charm_name"] == "cinder-volume-infinidat"
        assert result["charm_channel"] == "stable"
        assert result["charm_revision"] == 42
        assert result["charm_base"] == "ubuntu@24.04"

    def test_manifest_without_charm_entry_uses_defaults(
        self, infinidat_backend, mock_deployment, sample_config
    ):
        """When manifest has backend entry but no charm config, defaults are used."""
        backend_instance = StorageInstanceManifest(software=SoftwareConfig(charms={}))
        backend_manifests = StorageBackendManifests(
            root={"my-backend": backend_instance}
        )
        manifest = Manifest(
            storage=StorageManifest(root={"infinidat": backend_manifests})
        )
        result = infinidat_backend.build_terraform_vars(
            mock_deployment, manifest, "my-backend", sample_config
        )

        assert result["charm_name"] == "cinder-volume-infinidat"
        assert result["charm_channel"] == "latest/edge"
        assert result["charm_revision"] is None
        assert result["charm_base"] == "ubuntu@24.04"

    def test_channel_override_preserves_other_defaults(
        self, infinidat_backend, mock_deployment, sample_config
    ):
        """Channel override via standard manifest field works.

        Other fields stay default.
        """
        manifest = self._make_manifest(channel="stable")
        result = infinidat_backend.build_terraform_vars(
            mock_deployment, manifest, "my-backend", sample_config
        )

        assert result["charm_name"] == "cinder-volume-infinidat"
        assert result["charm_channel"] == "stable"
        assert result["charm_revision"] is None
        assert result["charm_base"] == "ubuntu@24.04"

    def test_revision_override_preserves_other_defaults(
        self, infinidat_backend, mock_deployment, sample_config
    ):
        """Revision override via standard manifest field works.

        Other fields stay default.
        """
        manifest = self._make_manifest(revision=42)
        result = infinidat_backend.build_terraform_vars(
            mock_deployment, manifest, "my-backend", sample_config
        )

        assert result["charm_name"] == "cinder-volume-infinidat"
        assert result["charm_channel"] == "latest/edge"
        assert result["charm_revision"] == 42

    def test_revision_without_channel_uses_default_channel(
        self, infinidat_backend, mock_deployment, sample_config
    ):
        """When only revision is set, channel falls back to the backend default."""
        manifest = self._make_manifest(revision=42)
        result = infinidat_backend.build_terraform_vars(
            mock_deployment, manifest, "my-backend", sample_config
        )

        assert result["charm_channel"] == "latest/edge"
        assert result["charm_revision"] == 42

    def test_different_backend_name_not_matched(
        self, infinidat_backend, mock_deployment, sample_config
    ):
        """Manifest entry for a different backend name doesn't affect result."""
        # Create manifest with a different backend name
        charm_manifest = CharmManifest(channel="stable")
        backend_instance = StorageInstanceManifest(
            software=SoftwareConfig(charms={"cinder-volume-infinidat": charm_manifest})
        )
        backend_manifests = StorageBackendManifests(
            root={"other-backend": backend_instance}
        )
        manifest = Manifest(
            storage=StorageManifest(root={"infinidat": backend_manifests})
        )

        result = infinidat_backend.build_terraform_vars(
            mock_deployment, manifest, "my-backend", sample_config
        )

        # Should use defaults since "my-backend" doesn't match "other-backend"
        assert result["charm_name"] == "cinder-volume-infinidat"
        assert result["charm_channel"] == "latest/edge"

    def test_tfvars_contains_expected_keys(
        self, infinidat_backend, mock_deployment, sample_config, empty_manifest
    ):
        """Verify the result dict contains all expected Terraform variable keys."""
        result = infinidat_backend.build_terraform_vars(
            mock_deployment, empty_manifest, "my-backend", sample_config
        )

        expected_keys = {
            "principal_application",
            "charm_name",
            "charm_base",
            "charm_channel",
            "charm_revision",
            "endpoint_bindings",
            "charm_config",
            "secrets",
        }
        assert set(result.keys()) == expected_keys


class TestInfinidatChapValidation:
    """Test CHAP authentication cross-field validation.

    When use_chap_auth is enabled, chap_username and chap_password
    must be provided or validation raises a blocked status error.
    """

    BASE_CONFIG = {
        "san-ip": "192.168.1.1",
        "infinidat-pool-name": "pool1",
        "san-login": "admin",
        "san-password": "secret",
    }

    def test_chap_enabled_both_missing_raises_blocked(self):
        """CHAP enabled with no credentials raises blocked error."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Blocked"):
            InfinidatConfig.model_validate(
                {**self.BASE_CONFIG, "use-chap-auth": True}
            )

    def test_chap_enabled_username_missing_raises_blocked(self):
        """CHAP enabled with only password raises blocked error."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="chap_username"):
            InfinidatConfig.model_validate(
                {
                    **self.BASE_CONFIG,
                    "use-chap-auth": True,
                    "chap-password": "pass",
                }
            )

    def test_chap_enabled_password_missing_raises_blocked(self):
        """CHAP enabled with only username raises blocked error."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="chap_password"):
            InfinidatConfig.model_validate(
                {
                    **self.BASE_CONFIG,
                    "use-chap-auth": True,
                    "chap-username": "user",
                }
            )

    def test_chap_enabled_both_provided_passes(self):
        """CHAP enabled with both credentials passes validation."""
        config = InfinidatConfig.model_validate(
            {
                **self.BASE_CONFIG,
                "use-chap-auth": True,
                "chap-username": "user",
                "chap-password": "pass",
            }
        )
        assert config.use_chap_auth is True
        assert config.chap_username == "user"
        assert config.chap_password == "pass"

    def test_chap_disabled_no_credentials_passes(self):
        """CHAP disabled without credentials passes validation."""
        config = InfinidatConfig.model_validate(
            {**self.BASE_CONFIG, "use-chap-auth": False}
        )
        assert config.use_chap_auth is False
        assert config.chap_username is None
        assert config.chap_password is None

    def test_chap_none_no_credentials_passes(self):
        """CHAP set to None without credentials passes validation."""
        config = InfinidatConfig.model_validate(
            {**self.BASE_CONFIG, "use-chap-auth": None}
        )
        assert config.use_chap_auth is None
        assert config.chap_username is None
        assert config.chap_password is None
