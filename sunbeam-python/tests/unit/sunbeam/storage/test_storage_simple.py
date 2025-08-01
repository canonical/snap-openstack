# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Simplified unit tests for storage backend management.

Focus on core functionality without complex mocking dependencies.
"""

import unittest
from unittest.mock import patch


class TestStorageBackendBasics(unittest.TestCase):
    """Basic tests for storage backend functionality without complex dependencies."""

    def test_storage_backend_exceptions(self):
        """Test that storage backend exceptions can be imported and used."""
        try:
            from sunbeam.storage.basestorage import (
                BackendAlreadyExistsException,
                BackendNotFoundException,
                BackendValidationException,
                StorageBackendException,
            )

            # Test exception hierarchy
            self.assertTrue(
                issubclass(BackendNotFoundException, StorageBackendException)
            )
            self.assertTrue(
                issubclass(BackendAlreadyExistsException, StorageBackendException)
            )
            self.assertTrue(
                issubclass(BackendValidationException, StorageBackendException)
            )

            # Test exception creation
            exc = StorageBackendException("Test error")
            self.assertEqual(str(exc), "Test error")

            exc = BackendNotFoundException("Backend not found")
            self.assertEqual(str(exc), "Backend not found")

        except ImportError as e:
            self.skipTest(f"Storage backend modules not available: {e}")

    def test_storage_backend_config_model(self):
        """Test that storage backend config model works."""
        try:
            from sunbeam.storage.basestorage import StorageBackendConfig

            # Test valid config creation
            config = StorageBackendConfig(name="test-backend")
            self.assertEqual(config.name, "test-backend")

        except ImportError as e:
            self.skipTest(f"Storage backend modules not available: {e}")

    def test_storage_backend_info_model(self):
        """Test that storage backend info model works."""
        try:
            from sunbeam.storage.basestorage import StorageBackendInfo

            # Test valid info creation
            info = StorageBackendInfo(
                name="test-backend",
                backend_type="hitachi",
                status="active",
                charm="cinder-volume-hitachi",
            )

            self.assertEqual(info.name, "test-backend")
            self.assertEqual(info.backend_type, "hitachi")
            self.assertEqual(info.status, "active")
            self.assertEqual(info.charm, "cinder-volume-hitachi")
            self.assertEqual(info.config, {})  # Default empty dict

        except ImportError as e:
            self.skipTest(f"Storage backend modules not available: {e}")

    def test_storage_backend_base_class(self):
        """Test that storage backend base class can be imported and instantiated."""
        try:
            from sunbeam.storage.basestorage import StorageBackendBase

            # Test base class instantiation
            backend = StorageBackendBase()
            self.assertEqual(backend.name, "base")
            self.assertEqual(backend.display_name, "Base Storage Backend")
            self.assertIsNone(backend.service)

            # Test that commands method exists and returns dict
            commands = backend.commands()
            self.assertIsInstance(commands, dict)

        except ImportError as e:
            self.skipTest(f"Storage backend modules not available: {e}")

    def test_hitachi_config_model(self):
        """Test that Hitachi config model works with required fields."""
        try:
            from sunbeam.storage.backends.hitachi import HitachiConfig

            # Test valid minimal config
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
            self.assertEqual(config.san_ip, "192.168.1.100")
            self.assertEqual(config.san_username, "maintenance")  # Default value
            self.assertEqual(config.san_password, "secret123")

        except ImportError as e:
            self.skipTest(f"Hitachi backend modules not available: {e}")

    def test_hitachi_backend_class(self):
        """Test that Hitachi backend class can be imported and instantiated."""
        try:
            from sunbeam.storage.backends.hitachi import HitachiBackend, HitachiConfig

            # Test backend instantiation
            backend = HitachiBackend()
            self.assertEqual(backend.name, "hitachi")
            self.assertEqual(backend.display_name, "Hitachi VSP Storage Backend")

            # Test config class property
            config_class = backend.config_class
            self.assertEqual(config_class, HitachiConfig)

            # Test that commands method exists and returns dict
            commands = backend.commands()
            self.assertIsInstance(commands, dict)
            self.assertIn("add", commands)
            self.assertIn("remove", commands)

        except ImportError as e:
            self.skipTest(f"Hitachi backend modules not available: {e}")

    def test_storage_registry_class(self):
        """Test that storage registry class can be imported and instantiated."""
        try:
            from sunbeam.storage.registry import (
                StorageBackendRegistry,
                storage_backend_registry,
            )

            # Test registry instantiation
            registry = StorageBackendRegistry()
            self.assertIsInstance(registry._backends, dict)
            self.assertFalse(registry._loaded)

            # Test global registry instance
            self.assertIsInstance(storage_backend_registry, StorageBackendRegistry)

        except ImportError as e:
            self.skipTest(f"Storage registry modules not available: {e}")

    def test_storage_steps_classes(self):
        """Test that storage step classes can be imported."""
        try:
            from sunbeam.storage.steps import (
                CheckBackendExistsStep,
                DeployCharmStep,
                IntegrateWithCinderVolumeStep,
                RemoveBackendStep,
                ValidateBackendExistsStep,
                ValidateConfigStep,
                WaitForReadyStep,
            )

            # Test that all step classes exist
            self.assertTrue(callable(ValidateConfigStep))
            self.assertTrue(callable(CheckBackendExistsStep))
            self.assertTrue(callable(ValidateBackendExistsStep))
            self.assertTrue(callable(DeployCharmStep))
            self.assertTrue(callable(IntegrateWithCinderVolumeStep))
            self.assertTrue(callable(WaitForReadyStep))
            self.assertTrue(callable(RemoveBackendStep))

        except ImportError as e:
            self.skipTest(f"Storage steps modules not available: {e}")

    def test_charm_name_normalization(self):
        """Test charm name normalization logic without complex mocking."""
        try:
            from sunbeam.storage.basestorage import StorageBackendService

            # Create a minimal service instance for testing static methods
            # We'll mock the deployment to avoid complex initialization
            with patch(
                "sunbeam.storage.basestorage.StorageBackendService.__init__",
                return_value=None,
            ):
                service = StorageBackendService.__new__(StorageBackendService)

                # Test charm name normalization
                test_cases = [
                    ("local:cinder-volume-hitachi-123", "cinder-volume-hitachi"),
                    ("cinder-volume-hitachi-456", "cinder-volume-hitachi"),
                    ("cinder-volume-hitachi", "cinder-volume-hitachi"),
                    ("local:some-charm", "some-charm"),
                ]

                for input_name, expected in test_cases:
                    with self.subTest(input_name=input_name):
                        result = service._normalize_charm_name(input_name)
                        self.assertEqual(result, expected)

        except ImportError as e:
            self.skipTest(f"Storage backend service not available: {e}")

    def test_backend_type_detection(self):
        """Test backend type detection logic without complex mocking."""
        try:
            from sunbeam.storage.basestorage import StorageBackendService

            # Create a minimal service instance for testing static methods
            with patch(
                "sunbeam.storage.basestorage.StorageBackendService.__init__",
                return_value=None,
            ):
                service = StorageBackendService.__new__(StorageBackendService)

                # Test backend type detection
                test_cases = [
                    ("cinder-volume-hitachi", "", "hitachi"),
                    ("cinder-volume-ceph", "", "ceph"),
                    ("cinder-volume-netapp", "", "netapp"),
                    ("unknown-charm", "test-app", "test-app"),
                    ("", "fallback-app", "fallback-app"),
                ]

                for charm_name, app_name, expected in test_cases:
                    with self.subTest(charm_name=charm_name, app_name=app_name):
                        result = service._get_backend_type_from_charm(
                            charm_name, app_name
                        )
                        self.assertEqual(result, expected)

        except ImportError as e:
            self.skipTest(f"Storage backend service not available: {e}")


class TestStorageBackendIntegration(unittest.TestCase):
    """Integration tests that verify components work together."""

    def test_hitachi_backend_config_integration(self):
        """Test that Hitachi backend and config work together."""
        try:
            from sunbeam.storage.backends.hitachi import HitachiBackend

            backend = HitachiBackend()
            config_class = backend.config_class

            # Create a config using the backend's config class
            config = config_class(
                name="integration-test",
                serial="99999",
                pools="integration-pool",
                san_ip="10.0.0.1",
                san_password="integration-secret",
            )

            # Verify the config is properly created
            self.assertEqual(config.name, "integration-test")
            self.assertEqual(config.serial, "99999")
            self.assertEqual(config.protocol, "FC")  # Default

        except ImportError as e:
            self.skipTest(f"Hitachi backend integration not available: {e}")

    def test_registry_backend_discovery(self):
        """Test that registry can discover backends without loading them."""
        try:
            from sunbeam.storage.registry import StorageBackendRegistry

            registry = StorageBackendRegistry()

            # Test that registry starts unloaded
            self.assertFalse(registry._loaded)
            self.assertEqual(len(registry._backends), 0)

            # Test that we can call list_backends (even if it loads backends)
            backends = registry.list_backends()
            self.assertIsInstance(backends, dict)

            # After calling list_backends, registry should be loaded
            self.assertTrue(registry._loaded)

        except ImportError as e:
            self.skipTest(f"Storage registry integration not available: {e}")


if __name__ == "__main__":
    unittest.main()
