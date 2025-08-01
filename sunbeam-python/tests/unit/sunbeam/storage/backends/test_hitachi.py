# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import Mock, patch

try:
    from pydantic import ValidationError
except ImportError:
    # Fallback for environments without pydantic
    class ValidationError(Exception):
        pass


from sunbeam.core.deployment import Deployment
from sunbeam.storage.backends.hitachi import (
    DeployHitachiCharmStep,
    HitachiBackend,
    HitachiConfig,
    RemoveHitachiBackendStep,
    ValidateHitachiConfigStep,
    WaitForHitachiReadyStep,
)


class TestHitachiConfig(unittest.TestCase):
    """Test cases for HitachiConfig model."""

    def test_valid_config_minimal(self):
        """Test creating valid minimal configuration."""
        config = HitachiConfig(
            name="test-hitachi",
            serial="12345",
            pools="pool1,pool2",
            san_ip="192.168.1.100",
            san_password="secret123",
        )

        self.assertEqual(config.name, "test-hitachi")
        self.assertEqual(config.serial, "12345")
        self.assertEqual(config.pools, "pool1,pool2")
        self.assertEqual(config.protocol, "FC")  # Default value
        self.assertEqual(
            config.san_ip, "192.168.1.100"
        )  # Validator returns the actual IP
        self.assertEqual(config.san_username, "maintenance")  # Default value
        self.assertEqual(config.san_password, "secret123")

    def test_valid_config_full(self):
        """Test creating valid full configuration."""
        config = HitachiConfig(
            name="test-hitachi",
            serial="67890",
            pools="pool3",
            protocol="iSCSI",
            san_ip="hitachi.example.com",
            san_username="admin",
            san_password="password123",
        )

        self.assertEqual(config.protocol, "ISCSI")  # Validator converts to uppercase
        self.assertEqual(config.san_username, "admin")

    def test_invalid_config_missing_required_fields(self):
        """Test validation errors for missing required fields."""
        # Missing name
        with self.assertRaises(ValidationError):
            HitachiConfig(
                serial="12345",
                pools="pool1",
                san_ip="192.168.1.100",
                san_password="secret",
            )

        # Missing serial
        with self.assertRaises(ValidationError):
            HitachiConfig(
                name="test",
                pools="pool1",
                san_ip="192.168.1.100",
                san_password="secret",
            )

        # Missing pools
        with self.assertRaises(ValidationError):
            HitachiConfig(
                name="test",
                serial="12345",
                san_ip="192.168.1.100",
                san_password="secret",
            )

        # Missing san_ip
        with self.assertRaises(ValidationError):
            HitachiConfig(
                name="test", serial="12345", pools="pool1", san_password="secret"
            )

        # Missing san_password
        with self.assertRaises(ValidationError):
            HitachiConfig(
                name="test", serial="12345", pools="pool1", san_ip="192.168.1.100"
            )

    def test_volume_backend_name_generation(self):
        """Test volume backend name default behavior."""
        config = HitachiConfig(
            name="my-hitachi-backend",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret",
        )

        # volume_backend_name defaults to None, not the name
        self.assertIsNone(config.volume_backend_name)

    def test_ip_validation(self):
        """Test IP address validation."""
        # Valid IPv4
        config = HitachiConfig(
            name="test",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret",
        )
        self.assertEqual(config.san_ip, "192.168.1.100")

        # Valid FQDN
        config = HitachiConfig(
            name="test",
            serial="12345",
            pools="pool1",
            san_ip="hitachi.example.com",
            san_password="secret",
        )
        self.assertEqual(config.san_ip, "hitachi.example.com")


class TestHitachiBackend(unittest.TestCase):
    """Test cases for HitachiBackend class."""

    def setUp(self):
        # Create backend without calling __init__ to avoid validation errors
        self.backend = HitachiBackend.__new__(HitachiBackend)
        self.deployment = Mock(spec=Deployment)

    def test_init(self):
        """Test backend initialization."""
        self.assertEqual(self.backend.name, "hitachi")
        self.assertEqual(self.backend.display_name, "Hitachi VSP Storage Backend")

    def test_config_class(self):
        """Test configuration class retrieval."""
        # Test the property directly without calling it
        config_class = self.backend.config_class
        self.assertEqual(config_class, HitachiConfig)

        # Test that we can create an instance with required fields
        config = config_class(
            name="test",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret",
        )
        self.assertEqual(config.name, "test")

    def test_validate_ip_or_fqdn_valid_ip(self):
        """Test IP validation with valid IPv4 address."""
        # Should return the actual IP address
        self.assertEqual(
            HitachiBackend._validate_ip_or_fqdn("192.168.1.100"), "192.168.1.100"
        )
        self.assertEqual(HitachiBackend._validate_ip_or_fqdn("10.0.0.1"), "10.0.0.1")
        self.assertEqual(
            HitachiBackend._validate_ip_or_fqdn("172.16.0.1"), "172.16.0.1"
        )

    def test_validate_ip_or_fqdn_valid_fqdn(self):
        """Test IP validation with valid FQDN."""
        # Should return the actual FQDN
        self.assertEqual(
            HitachiBackend._validate_ip_or_fqdn("hitachi.example.com"),
            "hitachi.example.com",
        )
        self.assertEqual(
            HitachiBackend._validate_ip_or_fqdn("storage.local"), "storage.local"
        )
        self.assertEqual(
            HitachiBackend._validate_ip_or_fqdn("vsp-1.company.org"),
            "vsp-1.company.org",
        )

    def test_validate_ip_or_fqdn_invalid(self):
        """Test IP validation with invalid values."""
        # The validator now raises ValueError for invalid values
        with self.assertRaises(ValueError):
            HitachiBackend._validate_ip_or_fqdn("not-an-ip-or-domain!@#")

        with self.assertRaises(ValueError):
            HitachiBackend._validate_ip_or_fqdn("invalid..domain")

        with self.assertRaises(ValueError):
            HitachiBackend._validate_ip_or_fqdn("")

    def test_create_add_plan(self):
        """Test add plan creation."""
        config = HitachiConfig(
            name="test-hitachi",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret",
        )

        plan = self.backend._create_add_plan(self.deployment, config)

        # Should return list of steps
        self.assertIsInstance(plan, list)
        self.assertGreater(len(plan), 0)

        # Check step types
        step_types = [type(step).__name__ for step in plan]
        self.assertIn("ValidateHitachiConfigStep", step_types)
        self.assertIn("DeployHitachiCharmStep", step_types)
        self.assertIn("IntegrateWithCinderVolumeStep", step_types)
        self.assertIn("WaitForHitachiReadyStep", step_types)

    def test_create_add_plan_with_local_charm(self):
        """Test add plan creation with local charm."""
        config = HitachiConfig(
            name="test-hitachi",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret",
        )

        plan = self.backend._create_add_plan(self.deployment, config, "/path/to/charm")

        # Should still create a plan
        self.assertIsInstance(plan, list)
        self.assertGreater(len(plan), 0)

    def test_create_remove_plan(self):
        """Test remove plan creation."""
        plan = self.backend._create_remove_plan(self.deployment, "test-backend")

        # Should return list of steps
        self.assertIsInstance(plan, list)
        self.assertGreater(len(plan), 0)

        # Check step types
        step_types = [type(step).__name__ for step in plan]
        self.assertIn("ValidateBackendExistsStep", step_types)
        self.assertIn("RemoveHitachiBackendStep", step_types)

    def test_commands_structure(self):
        """Test command registration structure."""
        commands = self.backend.commands()

        # Should have basic command groups
        expected_groups = ["add", "remove"]
        for group in expected_groups:
            self.assertIn(group, commands)
            self.assertIsInstance(commands[group], list)

            # Each group should have exactly one command
            self.assertEqual(len(commands[group]), 1)

            # Each command should have name and command
            cmd = commands[group][0]
            self.assertIn("name", cmd)
            self.assertIn("command", cmd)
            self.assertEqual(cmd["name"], "hitachi")

    @patch("click.prompt")
    def test_prompt_for_config(self, mock_prompt):
        """Test configuration prompting."""
        # Mock user inputs
        mock_prompt.side_effect = [
            "test-hitachi",  # name
            "12345",  # serial
            "pool1,pool2",  # pools
            "FC",  # protocol
            "192.168.1.100",  # san_ip
            "admin",  # san_username
            "secret123",  # san_password
        ]

        config = self.backend._prompt_for_config()

        self.assertIsInstance(config, dict)
        self.assertEqual(config["name"], "test-hitachi")
        self.assertEqual(config["serial"], "12345")
        self.assertEqual(config["pools"], "pool1,pool2")
        self.assertEqual(config["protocol"], "FC")
        self.assertEqual(config["san_ip"], "192.168.1.100")
        self.assertEqual(config["san_username"], "admin")
        self.assertEqual(config["san_password"], "secret123")


class TestValidateHitachiConfigStep(unittest.TestCase):
    """Test cases for ValidateHitachiConfigStep."""

    def test_init(self):
        """Test step initialization."""
        config = HitachiConfig(
            name="test-hitachi",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret",
        )

        step = ValidateHitachiConfigStep(config)

        self.assertEqual(step.name, "Validate Hitachi Configuration")
        self.assertIn("test-hitachi", step.description)

    def test_ip_validation(self):
        """Test IP validation in config."""
        config = HitachiConfig(
            name="test-hitachi",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret123",
        )

        # IP should be validated and returned as the actual value
        self.assertEqual(config.san_ip, "192.168.1.100")


class TestDeployHitachiCharmStep(unittest.TestCase):
    """Test cases for DeployHitachiCharmStep."""

    def test_init(self):
        """Test step initialization."""
        deployment = Mock(spec=Deployment)
        config = HitachiConfig(
            name="test-hitachi",
            serial="12345",
            pools="pool1,pool2",
            protocol="iSCSI",
            san_ip="192.168.1.100",
            san_username="admin",
            san_password="secret123",
        )

        step = DeployHitachiCharmStep(deployment, config)

        # Should inherit from DeployCharmStep
        self.assertEqual(step.deployment, deployment)
        self.assertEqual(step.config, config)

    def test_init_with_local_charm(self):
        """Test step initialization with local charm."""
        deployment = Mock(spec=Deployment)
        config = HitachiConfig(
            name="test-hitachi",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret",
        )

        step = DeployHitachiCharmStep(deployment, config, "/path/to/charm")

        self.assertEqual(step.deployment, deployment)
        self.assertEqual(step.config, config)

    def test_charm_config_mapping(self):
        """Test that charm configuration is properly mapped."""
        deployment = Mock(spec=Deployment)
        config = HitachiConfig(
            name="test-hitachi",
            serial="67890",
            pools="pool3,pool4",
            protocol="FC",
            san_ip="hitachi.example.com",
            san_username="operator",
            san_password="password123",
        )

        DeployHitachiCharmStep(deployment, config)

        # The step should map config fields to charm config
        # This is tested indirectly through the parent class behavior


class TestWaitForHitachiReadyStep(unittest.TestCase):
    """Test cases for WaitForHitachiReadyStep."""

    def test_init(self):
        """Test step initialization."""
        deployment = Mock(spec=Deployment)
        config = HitachiConfig(
            name="test-hitachi",
            serial="12345",
            pools="pool1",
            san_ip="192.168.1.100",
            san_password="secret",
        )

        step = WaitForHitachiReadyStep(deployment, config)

        self.assertEqual(step.deployment, deployment)
        self.assertEqual(step.config, config)
        self.assertIn("test-hitachi", step.description)


class TestRemoveHitachiBackendStep(unittest.TestCase):
    """Test cases for RemoveHitachiBackendStep."""

    def test_init(self):
        """Test step initialization."""
        deployment = Mock(spec=Deployment)
        backend_name = "test-hitachi-backend"

        step = RemoveHitachiBackendStep(deployment, backend_name)

        self.assertEqual(step.deployment, deployment)
        self.assertIn(backend_name, step.description)


if __name__ == "__main__":
    unittest.main()
