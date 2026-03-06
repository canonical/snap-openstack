# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for StorageBackendBase class.

These tests are designed to be generic and can be reused by child classes
by overriding the backend fixture.
"""

from unittest.mock import Mock, patch

import click
import pytest
from packaging.version import Version

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import (
    FQDN_PATTERN,
    JUJU_APP_NAME_PATTERN,
    validate_juju_application_name,
)


class TestJujuApplicationNameValidation:
    """Test Juju application name validation logic."""

    def test_valid_names(self):
        """Test valid Juju application names."""
        valid_names = [
            "myapp",
            "my-app",
            "my-app-backend",
            "a",
            "a1",
            "app123",
            "my-storage",
        ]
        for name in valid_names:
            assert validate_juju_application_name(name), f"{name} should be valid"

    def test_invalid_names(self):
        """Test invalid Juju application names."""
        invalid_names = [
            "",  # Empty
            "MyApp",  # Uppercase
            "my_app",  # Underscore
            "123app",  # Starts with number
            "-myapp",  # Starts with hyphen
            "myapp-",  # Ends with hyphen
            "my--app",  # Consecutive hyphens
            "my-app-1",  # Number after final hyphen
        ]
        for name in invalid_names:
            assert not validate_juju_application_name(name), f"{name} should be invalid"

    def test_pattern_matching(self):
        """Test the regex pattern directly."""
        assert JUJU_APP_NAME_PATTERN.match("myapp")
        assert not JUJU_APP_NAME_PATTERN.match("MyApp")
        assert not JUJU_APP_NAME_PATTERN.match("123app")


class TestFQDNPattern:
    """Test FQDN pattern validation."""

    def test_valid_fqdns(self):
        """Test valid FQDNs."""
        import re

        valid_fqdns = [
            "example.com",
            "sub.example.com",
            "my-server.example.com",
            "server1.example.com",
            "a.b.c.d.example.com",
        ]
        pattern = re.compile(FQDN_PATTERN)
        for fqdn in valid_fqdns:
            assert pattern.match(fqdn), f"{fqdn} should be valid"

    def test_invalid_fqdns(self):
        """Test invalid FQDNs."""
        import re

        invalid_fqdns = [
            "",
            "-example.com",
            "example-.com",
            "exa mple.com",
            "example..com",
        ]
        pattern = re.compile(FQDN_PATTERN)
        for fqdn in invalid_fqdns:
            assert not pattern.match(fqdn), f"{fqdn} should be invalid"


class BaseStorageBackendTests:
    """Base test class for storage backends.

    Subclasses can inherit from this to get comprehensive testing
    for their backend implementations.
    """

    @pytest.fixture
    def backend(self, mock_backend):
        """Override this fixture to test a specific backend."""
        return mock_backend

    def test_backend_type_is_set(self, backend):
        """Test that backend_type is set."""
        assert backend.backend_type
        assert isinstance(backend.backend_type, str)
        assert backend.backend_type != "base"

    def test_display_name_is_set(self, backend):
        """Test that display_name is set."""
        assert backend.display_name
        assert isinstance(backend.display_name, str)

    def test_version_is_set(self, backend):
        """Test that version is set."""
        assert backend.version
        assert isinstance(backend.version, Version)

    def test_charm_name_is_set(self, backend):
        """Test that charm_name is set."""
        assert backend.charm_name
        assert isinstance(backend.charm_name, str)

    def test_charm_channel_is_set(self, backend):
        """Test that charm_channel is set."""
        assert backend.charm_channel
        assert isinstance(backend.charm_channel, str)

    def test_charm_base_is_set(self, backend):
        """Test that charm_base is set."""
        assert backend.charm_base
        assert isinstance(backend.charm_base, str)

    def test_principal_application_is_set(self, backend):
        """Test that principal_application is set."""
        assert backend.principal_application
        assert isinstance(backend.principal_application, str)

    def test_tfplan_properties(self, backend):
        """Test Terraform plan properties."""
        assert backend.tfplan
        assert backend.tfplan_dir
        assert isinstance(backend.tfplan, str)
        assert isinstance(backend.tfplan_dir, str)

    def test_tfvar_config_key(self, backend):
        """Test tfvar config key."""
        assert backend.tfvar_config_key == "TerraformVarsStorageBackends"

    def test_config_key(self, backend):
        """Test config key generation."""
        name = "test-backend"
        key = backend.config_key(name)
        assert key == f"Storage-{name}"

    def test_config_type_returns_pydantic_model(self, backend):
        """Test that config_type returns a Pydantic model class."""
        config_class = backend.config_type()
        assert issubclass(config_class, StorageBackendConfig)

    def test_get_endpoint_bindings(self, backend, mock_deployment):
        """Test endpoint bindings generation."""
        bindings = backend.get_endpoint_bindings(mock_deployment)
        assert isinstance(bindings, list)
        assert len(bindings) >= 1
        for binding in bindings:
            assert isinstance(binding, dict)

    def test_validate_ip_or_fqdn_with_valid_ip(self, backend):
        """Test IP validation with valid IPs."""
        valid_ips = ["192.168.1.1", "10.0.0.1", "2001:db8::1"]
        for ip in valid_ips:
            assert backend._validate_ip_or_fqdn(ip) == ip

    def test_validate_ip_or_fqdn_with_valid_fqdn(self, backend):
        """Test FQDN validation with valid FQDNs."""
        valid_fqdns = ["example.com", "server.example.com", "my-server.local"]
        for fqdn in valid_fqdns:
            assert backend._validate_ip_or_fqdn(fqdn) == fqdn

    def test_validate_ip_or_fqdn_with_invalid_value(self, backend):
        """Test IP/FQDN validation with invalid values."""
        invalid_values = ["not an ip", "example..com", "", "-invalid.com"]
        for value in invalid_values:
            with pytest.raises(click.BadParameter):
                backend._validate_ip_or_fqdn(value)


class TestStorageBackendBase(BaseStorageBackendTests):
    """Tests for the base StorageBackendBase class using mock backend."""

    def test_register_terraform_plan(self, backend, mock_deployment, tmp_path):
        """Test Terraform plan registration raises error when plan not found."""
        # Mock the deployment's plan directory
        mock_deployment.plans_directory = tmp_path / "plans"
        mock_deployment.plans_directory.mkdir(parents=True, exist_ok=True)

        # Without a valid plan source, should raise FileNotFoundError
        with pytest.raises(FileNotFoundError):
            backend.register_terraform_plan(mock_deployment)

    def test_add_backend_instance_success(
        self, backend, mock_deployment, mock_console, tmp_path
    ):
        """Test adding a backend instance successfully."""
        # Setup
        backend_name = "test-backend"
        config = {"required_field": "value", "secret_field": "secret"}

        # Mock the manifest property
        mock_manifest = Mock()
        mock_manifest.storage.root = {}

        # Mock the service and JujuHelper
        with patch("sunbeam.storage.base.JujuHelper") as mock_jhelper_class:
            # Patch the manifest property without accessing it
            with patch.object(
                type(backend),
                "manifest",
                new_callable=lambda: property(lambda self: mock_manifest),
            ):
                mock_jhelper = Mock()
                mock_jhelper_class.return_value = mock_jhelper

                # Mock register_terraform_plan
                with patch.object(backend, "register_terraform_plan"):
                    # Mock run_plan
                    with patch("sunbeam.storage.base.run_plan"):
                        backend.add_backend_instance(
                            mock_deployment, backend_name, config, mock_console
                        )

    def test_add_backend_instance_invalid_name(
        self, backend, mock_deployment, mock_console
    ):
        """Test adding a backend with invalid name."""
        invalid_names = ["MyApp", "app_name", "123app", "app-"]

        for invalid_name in invalid_names:
            with pytest.raises(click.ClickException) as exc_info:
                backend.add_backend_instance(
                    mock_deployment, invalid_name, {}, mock_console
                )
            assert "Invalid backend name" in str(exc_info.value)

    def test_remove_backend(self, backend, mock_deployment, mock_console, tmp_path):
        """Test removing a backend."""
        backend_name = "test-backend"

        # Mock the manifest property
        mock_manifest = Mock()
        mock_manifest.storage.root = {}

        # Mock the plan directory
        mock_deployment.plans_directory = tmp_path / "plans"
        mock_deployment.plans_directory.mkdir(parents=True, exist_ok=True)

        with patch.object(
            type(backend),
            "manifest",
            new_callable=lambda: property(lambda self: mock_manifest),
        ):
            with patch("sunbeam.storage.base.JujuHelper") as mock_jhelper_class:
                mock_jhelper = Mock()
                mock_jhelper_class.return_value = mock_jhelper

                with patch.object(backend, "register_terraform_plan"):
                    with patch("sunbeam.storage.base.run_plan"):
                        backend.remove_backend(
                            mock_deployment, backend_name, mock_console
                        )

    def test_build_terraform_vars(self, backend, mock_deployment, mock_manifest):
        """Test Terraform variables generation."""
        backend_name = "test-backend"
        config = backend.config_type().model_validate(
            {
                "required-field": "test",
                "secret-field": "secret123",
            }
        )

        tfvars = backend.build_terraform_vars(
            mock_deployment, mock_manifest, backend_name, config
        )

        assert "principal_application" in tfvars
        assert tfvars["principal_application"] == backend.principal_application
        assert "charm_name" in tfvars
        assert tfvars["charm_name"] == backend.charm_name
        assert "charm_channel" in tfvars
        assert "charm_base" in tfvars
        assert "endpoint_bindings" in tfvars
        assert "charm_config" in tfvars
        assert "secrets" in tfvars

    def test_display_config_options(self, backend, mock_console):
        """Test display of configuration options."""
        with patch("sunbeam.storage.base.console", mock_console):
            backend.display_config_options()
            # Verify console.print was called
            assert mock_console.print.called

    def test_display_config_table(self, backend, mock_console):
        """Test display of configuration table."""
        backend_name = "test-backend"
        config = backend.config_type().model_validate(
            {
                "required-field": "test_value",
                "secret-field": "secret123",
                "optional-field": "optional_value",
            }
        )

        with patch("sunbeam.storage.base.console", mock_console):
            backend.display_config_table(backend_name, config)
            # Verify console.print was called
            assert mock_console.print.called

    def test_display_config_table_with_empty_config(self, backend, mock_console):
        """Test display of empty configuration."""
        backend_name = "test-backend"
        config = None

        with patch("sunbeam.storage.base.console", mock_console):
            backend.display_config_table(backend_name, config)
            # Should still print something
            assert mock_console.print.called

    def test_format_config_value_non_secret(self, backend):
        """Test formatting of non-secret configuration values."""
        value = "test_value"
        formatted = backend._format_config_value(value, is_secret=False)
        assert formatted == "test_value"

    def test_format_config_value_secret(self, backend):
        """Test formatting of secret configuration values."""
        value = "secret123"
        formatted = backend._format_config_value(value, is_secret=True)
        assert formatted == "********"

    def test_format_config_value_long(self, backend):
        """Test formatting of long configuration values."""
        value = "a" * 30
        formatted = backend._format_config_value(value, is_secret=False)
        assert formatted.endswith("...")
        assert len(formatted) == 23

    def test_field_is_secret(self, backend):
        """Test detection of secret fields."""
        from sunbeam.storage.models import SecretDictField

        config_class = backend.config_type()
        for field_name, field_info in config_class.model_fields.items():
            is_secret = backend._field_is_secret(field_info)
            if field_name == "secret_field":
                assert is_secret
            else:
                # Check if it's actually marked as secret
                has_secret_metadata = any(
                    isinstance(m, SecretDictField) for m in field_info.metadata
                )
                assert is_secret == has_secret_metadata

    def test_get_field_descriptions(self, backend):
        """Test extraction of field descriptions."""
        config_class = backend.config_type()
        descriptions = backend._get_field_descriptions(config_class)

        assert isinstance(descriptions, dict)
        for field_name in config_class.model_fields.keys():
            assert field_name in descriptions
            assert isinstance(descriptions[field_name], str)

    def test_extract_field_info(self, backend):
        """Test extraction of field information."""
        config_class = backend.config_type()
        for field_name, field_info in config_class.model_fields.items():
            field_type, description = backend._extract_field_info(field_info)
            assert isinstance(field_type, str)
            assert isinstance(description, str)

    def test_create_deploy_step(self, backend, mock_deployment, mock_jhelper):
        """Test creation of deploy step."""
        from sunbeam.storage.steps import BaseStorageBackendDeployStep

        mock_client = Mock()
        mock_tfhelper = Mock()
        mock_manifest = Mock()
        preseed = {}
        backend_name = "test-backend"
        model = "openstack"

        step = backend.create_deploy_step(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            preseed,
            backend_name,
            model,
        )

        assert isinstance(step, BaseStorageBackendDeployStep)

    def test_create_destroy_step(self, backend, mock_deployment, mock_jhelper):
        """Test creation of destroy step."""
        from sunbeam.storage.steps import BaseStorageBackendDestroyStep

        mock_client = Mock()
        mock_tfhelper = Mock()
        mock_manifest = Mock()
        backend_name = "test-backend"
        model = "openstack"

        step = backend.create_destroy_step(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            model,
        )

        assert isinstance(step, BaseStorageBackendDestroyStep)

    def test_register_add_cli(self, backend):
        """Test CLI registration."""
        mock_add_group = Mock(spec=click.Group)

        with patch.object(backend, "_get_cli_class") as mock_get_cli_class:
            mock_cli_class = Mock()
            mock_cli_instance = Mock()
            mock_cli_class.return_value = mock_cli_instance
            mock_get_cli_class.return_value = mock_cli_class

            backend.register_add_cli(mock_add_group)

            mock_cli_class.assert_called_once_with(backend)
            mock_cli_instance.register_add_cli.assert_called_once_with(mock_add_group)

    def test_get_cli_class_default(self, backend):
        """Test getting CLI class with default implementation."""
        from sunbeam.storage.cli_base import StorageBackendCLIBase

        # For mock backend, it should return the base CLI class
        cli_class = backend._get_cli_class()
        assert cli_class == StorageBackendCLIBase

    def test_manifest_property(self, backend, mock_click_context):
        """Test manifest property."""
        with patch("click.get_current_context", return_value=mock_click_context):
            manifest = backend.manifest
            assert manifest is not None

    def test_manifest_property_caching(self, backend, mock_click_context):
        """Test that manifest is cached after first access."""
        with patch("click.get_current_context", return_value=mock_click_context):
            manifest1 = backend.manifest
            manifest2 = backend.manifest
            # Should be the same object (cached)
            assert manifest1 is manifest2

    def test_manifest_property_failure(self, backend, mock_click_context):
        """Test manifest property when loading fails."""
        mock_click_context.obj.get_manifest.return_value = None

        with patch("click.get_current_context", return_value=mock_click_context):
            with pytest.raises(ValueError, match="Failed to load manifest"):
                _ = backend.manifest

    def test_is_enabled_generally_available(self, backend):
        """Test is_enabled when backend is generally available."""
        backend.generally_available = True
        mock_client = Mock()
        mock_snap = Mock()

        result = backend.check_enabled(mock_client, mock_snap)
        assert result is True

    def test_is_enabled_via_clusterd_config(self, backend):
        """Test is_enabled via clusterd configuration."""
        import json

        backend.generally_available = False
        mock_client = Mock()
        mock_snap = Mock()

        # Feature gate not found in cluster DB
        mock_client.cluster.get_feature_gate.side_effect = Exception("Not found")
        mock_client.cluster.get_config.return_value = json.dumps([backend.backend_type])

        result = backend.check_enabled(mock_client, mock_snap)
        assert result is True
        mock_client.cluster.get_config.assert_called_once_with("StorageBackendsEnabled")

    def test_is_enabled_via_snap_config(self, backend):
        """Test is_enabled via snap configuration."""
        from sunbeam.clusterd.service import ConfigItemNotFoundException

        backend.generally_available = False
        mock_client = Mock()
        mock_snap = Mock()

        # Feature gate not found in cluster DB
        mock_client.cluster.get_feature_gate.side_effect = Exception("Not found")
        # Clusterd config not found
        mock_client.cluster.get_config.side_effect = ConfigItemNotFoundException("")

        # Snap config returns True
        mock_snap.config.get.return_value = True

        result = backend.check_enabled(mock_client, mock_snap)
        assert result is True
        mock_snap.config.get.assert_called_once_with(backend._feature_key)

    def test_is_enabled_disabled(self, backend):
        """Test is_enabled when backend is disabled."""
        from snaphelpers import UnknownConfigKey

        from sunbeam.clusterd.service import ConfigItemNotFoundException

        backend.generally_available = False
        mock_client = Mock()
        mock_snap = Mock()

        # Feature gate not found in cluster DB
        mock_client.cluster.get_feature_gate.side_effect = Exception("Not found")
        # Clusterd config not found
        mock_client.cluster.get_config.side_effect = ConfigItemNotFoundException("")

        # Snap config key not found
        mock_snap.config.get.side_effect = UnknownConfigKey("")

        result = backend.check_enabled(mock_client, mock_snap)
        assert result is False

    def test_enable_backend_new(self, backend):
        """Test enabling a backend that is not yet enabled."""
        import json

        from sunbeam.clusterd.service import ConfigItemNotFoundException

        mock_client = Mock()

        # Simulate no existing config
        mock_client.cluster.get_config.side_effect = ConfigItemNotFoundException("")

        backend.enable_backend(mock_client)

        # Should create new config with this backend
        mock_client.cluster.update_config.assert_called_once_with(
            "StorageBackendsEnabled", json.dumps([backend.backend_type])
        )

    def test_enable_backend_already_in_list(self, backend):
        """Test enabling a backend that is already enabled."""
        import json

        mock_client = Mock()

        # Simulate existing config with this backend already enabled
        mock_client.cluster.get_config.return_value = json.dumps(
            [backend.backend_type, "other-backend"]
        )

        backend.enable_backend(mock_client)

        # Should not update since already present
        mock_client.cluster.update_config.assert_not_called()

    def test_enable_backend_add_to_existing_list(self, backend):
        """Test enabling a backend when other backends are already enabled."""
        import json

        mock_client = Mock()

        # Simulate existing config with other backends
        mock_client.cluster.get_config.return_value = json.dumps(["other-backend"])

        backend.enable_backend(mock_client)

        # Should add this backend to the list
        expected_backends = ["other-backend", backend.backend_type]
        mock_client.cluster.update_config.assert_called_once_with(
            "StorageBackendsEnabled", json.dumps(expected_backends)
        )
