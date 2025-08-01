# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Hitachi storage backend implementation."""

from unittest.mock import patch

import click
import pytest
from pydantic import ValidationError

from sunbeam.storage.backends.hitachi.backend import (
    HitachiBackend,
    HitachiConfig,
    HitachiDeployStep,
    HitachiDestroyStep,
    HitachiUpdateConfigStep,
)
from sunbeam.storage.models import StorageBackendConfig


class TestHitachiConfig:
    """Test cases for HitachiConfig model."""

    def test_valid_config_minimal(self):
        """Test creating valid minimal Hitachi configuration."""
        config = HitachiConfig(
            name="hitachi-backend-1",
            hitachi_storage_id="123456",
            hitachi_pools="pool1,pool2",
            san_ip="192.168.1.100",
        )

        assert config.name == "hitachi-backend-1"
        assert config.hitachi_storage_id == "123456"
        assert config.hitachi_pools == "pool1,pool2"
        assert config.san_ip == "192.168.1.100"
        assert config.protocol == "FC"  # Default value

    def test_valid_config_full(self):
        """Test creating valid full Hitachi configuration."""
        config = HitachiConfig(
            name="hitachi-backend-1",
            hitachi_storage_id="123456",
            hitachi_pools="pool1,pool2",
            san_ip="192.168.1.100",
            protocol="iSCSI",
        )

        assert config.name == "hitachi-backend-1"
        assert config.hitachi_storage_id == "123456"
        assert config.hitachi_pools == "pool1,pool2"
        assert config.san_ip == "192.168.1.100"
        assert config.protocol == "iSCSI"

    def test_config_with_iscsi_protocol(self):
        """Test configuration with iSCSI protocol."""
        config = HitachiConfig(
            name="hitachi-iscsi",
            hitachi_storage_id="123456",
            hitachi_pools="pool1",
            san_ip="192.168.1.100",
            protocol="iSCSI",
        )

        assert config.protocol == "iSCSI"

    def test_config_validation_missing_required_fields(self):
        """Test validation errors for missing required fields."""
        # Missing hitachi_storage_id
        with pytest.raises(ValidationError):
            HitachiConfig(name="test", hitachi_pools="pool1", san_ip="192.168.1.100")

        # Missing hitachi_pools
        with pytest.raises(ValidationError):
            HitachiConfig(
                name="test", hitachi_storage_id="123456", san_ip="192.168.1.100"
            )

        # Missing san_ip
        with pytest.raises(ValidationError):
            HitachiConfig(
                name="test", hitachi_storage_id="123456", hitachi_pools="pool1"
            )

    def test_ip_validation_valid_ip(self):
        """Test valid IP address validation."""
        config = HitachiConfig(
            name="test",
            hitachi_storage_id="123456",
            hitachi_pools="pool1",
            san_ip="192.168.1.100",
        )
        assert config.san_ip == "192.168.1.100"

    def test_ip_validation_valid_fqdn(self):
        """Test valid FQDN validation."""
        config = HitachiConfig(
            name="test",
            hitachi_storage_id="123456",
            hitachi_pools="pool1",
            san_ip="storage.example.com",
        )
        assert config.san_ip == "storage.example.com"

    def test_protocol_validation(self):
        """Test protocol field validation."""
        # Valid protocols
        for protocol in ["FC", "iSCSI"]:
            config = HitachiConfig(
                name="test",
                hitachi_storage_id="123456",
                hitachi_pools="pool1",
                san_ip="192.168.1.100",
                protocol=protocol,
            )
            assert config.protocol == protocol

    def test_config_serialization(self):
        """Test configuration serialization."""
        config = HitachiConfig(
            name="test",
            hitachi_storage_id="123456",
            hitachi_pools="pool1",
            san_ip="192.168.1.100",
        )

        data = config.model_dump()
        assert data["name"] == "test"
        assert data["hitachi_storage_id"] == "123456"
        assert data["hitachi_pools"] == "pool1"
        assert data["san_ip"] == "192.168.1.100"
        assert data["protocol"] == "FC"

    def test_config_inheritance(self):
        """Test that HitachiConfig inherits from StorageBackendConfig."""
        config = HitachiConfig(
            name="test",
            hitachi_storage_id="123456",
            hitachi_pools="pool1",
            san_ip="192.168.1.100",
        )

        assert isinstance(config, StorageBackendConfig)


class TestHitachiBackend:
    """Test cases for HitachiBackend class."""

    def test_init(self):
        """Test backend initialization."""
        backend = HitachiBackend()

        assert backend.name == "hitachi"
        assert backend.display_name == "Hitachi VSP Storage"
        assert backend.charm_name == "cinder-volume-hitachi"
        assert backend.tfplan == "hitachi-backend-plan"
        assert backend.tfplan_dir == "deploy-hitachi-backend"
        assert (
            backend.charm_channel == "latest/edge"
        )  # this have to be updated after the charm progress to stable
        assert backend.charm_revision == 2
        assert backend.charm_base == "ubuntu@24.04"
        assert backend.backend_endpoint == "cinder-volume"
        assert backend.units == 1
        assert backend.additional_integrations == []

    def test_config_class(self):
        """Test configuration class retrieval."""
        backend = HitachiBackend()
        config_class = backend.config_class
        assert config_class == HitachiConfig

    def test_get_field_mapping(self):
        """Test field mapping for charm configuration."""
        backend = HitachiBackend()
        mapping = backend.get_field_mapping()

        # Test some key mappings
        assert mapping["hitachi_storage_id"] == "hitachi-storage-id"
        assert mapping["hitachi_pools"] == "hitachi-pools"
        assert mapping["san_ip"] == "san-ip"
        assert mapping["protocol"] == "protocol"
        assert mapping["use_chap_auth"] == "use-chap-auth"

    def test_get_terraform_variables(self):
        """Test Terraform variables generation."""
        backend = HitachiBackend()
        config = HitachiConfig(
            name="hitachi-backend-1",
            hitachi_storage_id="123456",
            hitachi_pools="pool1,pool2",
            san_ip="192.168.1.100",
        )

        variables = backend.get_terraform_variables(
            "hitachi-backend-1", config, "openstack"
        )

        assert "machine_model" in variables
        assert "hitachi_backends" in variables
        assert variables["machine_model"] == "openstack"
        assert "hitachi-backend-1" in variables["hitachi_backends"]

        backend_config = variables["hitachi_backends"]["hitachi-backend-1"]
        assert "charm_config" in backend_config

        # Verify charm config contains the expected fields
        charm_config = backend_config["charm_config"]
        assert charm_config["hitachi-storage-id"] == "123456"
        assert charm_config["hitachi-pools"] == "pool1,pool2"
        assert charm_config["san-ip"] == "192.168.1.100"

    def test_get_backend_config_filtering(self):
        """Test backend configuration filtering."""
        backend = HitachiBackend()
        config = HitachiConfig(
            name="hitachi-backend-1",
            hitachi_storage_id="123456",
            hitachi_pools="pool1,pool2",
            san_ip="192.168.1.100",
            protocol="iSCSI",  # Non-default value
        )

        backend_config = backend._get_backend_config(config)

        # Should include non-default values
        assert "hitachi-storage-id" in backend_config
        assert "hitachi-pools" in backend_config
        assert "san-ip" in backend_config
        assert "protocol" in backend_config  # Non-default value

        # Verify actual values
        assert backend_config["hitachi-storage-id"] == "123456"
        assert backend_config["hitachi-pools"] == "pool1,pool2"
        assert backend_config["san-ip"] == "192.168.1.100"
        assert backend_config["protocol"] == "iSCSI"

    def test_should_include_config_value(self):
        """Test configuration value inclusion logic."""
        backend = HitachiBackend()

        # Non-default string value should be included
        assert backend._should_include_config_value("san-ip", "192.168.1.100", "")

        # Default string value should not be included
        assert not backend._should_include_config_value(
            "san-login", "maintenance", "maintenance"
        )

        # Empty string should not be included
        assert not backend._should_include_config_value("san-ip", "", "")

        # Non-empty list should be included
        assert backend._should_include_config_value("hitachi-pools", ["pool1"], [])

        # Empty list should not be included
        assert not backend._should_include_config_value("hitachi-pools", [], [])

        # None values should not be included
        assert not backend._should_include_config_value("optional-field", None, None)

    def test_commands(self):
        """Test command registration structure."""
        backend = HitachiBackend()
        commands = backend.commands()

        assert isinstance(commands, dict)
        # Current implementation returns empty dict
        assert commands == {}

    def test_create_deploy_step(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test deployment step creation."""
        backend = HitachiBackend()
        config = HitachiConfig(
            name="test",
            hitachi_storage_id="123456",
            hitachi_pools="pool1",
            san_ip="192.168.1.100",
        )

        step = backend.create_deploy_step(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            "test-backend",
            config,
            "openstack",
        )

        assert isinstance(step, HitachiDeployStep)
        assert step.backend_name == "test-backend"
        assert step.backend_config == config
        assert step.model == "openstack"

    def test_create_destroy_step(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test destruction step creation."""
        backend = HitachiBackend()

        step = backend.create_destroy_step(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            "test-backend",
            "openstack",
        )

        assert isinstance(step, HitachiDestroyStep)
        assert step.backend_name == "test-backend"
        assert step.model == "openstack"

    def test_create_update_config_step(self, mock_deployment):
        """Test configuration update step creation."""
        backend = HitachiBackend()
        config_updates = {"key1": "value1", "key2": "value2"}

        step = backend.create_update_config_step(
            mock_deployment, "test-backend", config_updates
        )

        assert isinstance(step, HitachiUpdateConfigStep)
        assert step.backend_name == "test-backend"
        assert step.config_updates == config_updates

    @patch("click.confirm")
    @patch("click.prompt")
    def test_prompt_for_config(self, mock_prompt, mock_confirm, mock_deployment):
        """Test configuration prompting."""
        backend = HitachiBackend()

        # Mock user inputs for all prompts in _prompt_for_config
        mock_prompt.side_effect = [
            "123456",  # hitachi_storage_id (Array serial number)
            "pool1,pool2",  # hitachi_pools (Storage pools)
            "FC",  # protocol
            "192.168.1.100",  # san_ip (Management IP/FQDN)
            "maintenance",  # san_username (SAN username)
            "secret123",  # san_password (SAN password)
            "test-backend",  # volume_backend_name
        ]

        # Mock confirmation prompts (all False to avoid optional config)
        mock_confirm.side_effect = [
            False,  # configure_mirror (Configure mirror/replication)
        ]

        config = backend.prompt_for_config("test-backend")

        assert isinstance(config, HitachiConfig)
        assert config.name == "test-backend"
        assert config.hitachi_storage_id == "123456"
        assert config.hitachi_pools == "pool1,pool2"
        assert config.san_ip == "192.168.1.100"
        # Protocol should use default value
        assert config.protocol == "FC"

    def test_validate_ip_or_fqdn_valid_ip(self):
        """Test IP validation with valid IP."""
        # Should not raise exception
        HitachiBackend._validate_ip_or_fqdn("192.168.1.100")

    def test_validate_ip_or_fqdn_valid_fqdn(self):
        """Test IP validation with valid FQDN."""
        # Should not raise exception
        HitachiBackend._validate_ip_or_fqdn("storage.example.com")

    def test_validate_ip_or_fqdn_invalid(self):
        """Test IP validation with invalid input."""
        with pytest.raises(click.BadParameter):
            HitachiBackend._validate_ip_or_fqdn("..invalid..")


class TestHitachiSteps:
    """Test cases for Hitachi-specific step implementations."""

    def test_hitachi_deploy_step_init(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test HitachiDeployStep initialization."""
        backend_instance = HitachiBackend()
        backend_name = "hitachi-backend-1"
        model = "openstack"

        config = HitachiConfig(
            name=backend_name,
            hitachi_storage_id="123456",
            hitachi_pools="pool1,pool2",
            san_ip="192.168.1.100",
            protocol="FC",
        )

        step = HitachiDeployStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            config,
            backend_instance,
            model,
        )

        assert step.backend_name == backend_name
        assert step.backend_config == config
        assert step.backend_instance == backend_instance
        assert step.model == model

    def test_hitachi_deploy_step_get_terraform_variables(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test Terraform variables generation in deploy step."""
        backend_instance = HitachiBackend()
        backend_name = "hitachi-backend-1"
        model = "openstack"

        config = HitachiConfig(
            name=backend_name,
            hitachi_storage_id="123456",
            hitachi_pools="pool1,pool2",
            san_ip="192.168.1.100",
            protocol="FC",
        )

        step = HitachiDeployStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            config,
            backend_instance,
            model,
        )

        variables = step.get_terraform_variables()

        assert "machine_model" in variables
        assert "hitachi_backends" in variables
        assert variables["machine_model"] == "openstack"
        assert "hitachi-backend-1" in variables["hitachi_backends"]

        backend_config = variables["hitachi_backends"]["hitachi-backend-1"]
        assert "charm_config" in backend_config

    def test_hitachi_destroy_step_init(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test HitachiDestroyStep initialization."""
        backend_instance = HitachiBackend()
        backend_name = "hitachi-backend-1"
        model = "openstack"

        step = HitachiDestroyStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_instance,
            model,
        )

        assert step.backend_name == backend_name
        assert step.backend_instance == backend_instance
        assert step.model == model

    def test_hitachi_update_config_step_init(self, mock_deployment):
        """Test HitachiUpdateConfigStep initialization."""
        backend_instance = HitachiBackend()
        backend_name = "hitachi-backend-1"
        config_updates = {"san-ip": "192.168.1.101"}

        step = HitachiUpdateConfigStep(
            mock_deployment, backend_instance, backend_name, config_updates
        )

        assert step.backend_name == backend_name
        assert step.backend_instance == backend_instance
        assert step.config_updates == config_updates

    def test_hitachi_deploy_step_hooks(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test Hitachi deploy step hooks."""
        backend_instance = HitachiBackend()
        backend_name = "hitachi-backend-1"
        model = "openstack"

        config = HitachiConfig(
            name=backend_name,
            hitachi_storage_id="123456",
            hitachi_pools="pool1",
            san_ip="192.168.1.100",
            protocol="FC",
        )

        step = HitachiDeployStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            config,
            backend_instance,
            model,
        )

        # Test hooks don't raise errors
        step.pre_deploy_hook()
        step.post_deploy_hook()

    def test_hitachi_destroy_step_hooks(
        self, mock_deployment, mock_client, mock_tfhelper, mock_jhelper, mock_manifest
    ):
        """Test Hitachi destroy step hooks."""
        backend_instance = HitachiBackend()
        backend_name = "hitachi-backend-1"
        model = "openstack"

        step = HitachiDestroyStep(
            mock_deployment,
            mock_client,
            mock_tfhelper,
            mock_jhelper,
            mock_manifest,
            backend_name,
            backend_instance,
            model,
        )

        # Test hooks don't raise errors
        step.pre_destroy_hook()
        step.post_destroy_hook()

    def test_hitachi_update_config_step_hooks(self, mock_deployment):
        """Test Hitachi update config step hooks."""
        backend_instance = HitachiBackend()
        backend_name = "hitachi-backend-1"
        config_updates = {"san-ip": "192.168.1.101"}

        step = HitachiUpdateConfigStep(
            mock_deployment, backend_instance, backend_name, config_updates
        )

        # Test hooks don't raise errors
        step.pre_update_hook()
        step.post_update_hook()
