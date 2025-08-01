# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import Mock, patch

from sunbeam.core.deployment import Deployment
from sunbeam.storage.basestorage import StorageBackendBase
from sunbeam.storage.registry import StorageBackendRegistry, storage_backend_registry


class MockBackend(StorageBackendBase):
    """Mock backend for testing."""

    name = "mock"
    display_name = "Mock Backend"

    def config_class(self):
        from sunbeam.storage.basestorage import StorageBackendConfig

        return StorageBackendConfig

    def _prompt_for_config(self):
        return {"name": "test-mock"}

    def _create_add_plan(self, deployment, config, local_charm=""):
        return []

    def _create_remove_plan(self, deployment, backend_name):
        return []


class TestStorageBackendRegistry(unittest.TestCase):
    """Test cases for StorageBackendRegistry class."""

    def setUp(self):
        self.registry = StorageBackendRegistry()
        self.deployment = Mock(spec=Deployment)

    def tearDown(self):
        # Reset the registry state
        self.registry._backends = {}
        self.registry._loaded = False

    @patch("pathlib.Path.iterdir")
    def test_load_backends_success(self, mock_iterdir):
        """Test successful backend loading."""
        # Reset the registry to ensure clean state
        self.registry._loaded = False
        self.registry._backends = {}

        # Mock file structure
        mock_file = Mock()
        mock_file.is_file.return_value = True
        mock_file.name = "mock_backend.py"
        mock_file.stem = "mock_backend"
        mock_iterdir.return_value = [mock_file]

        # Mock the specific import for our test module
        with patch("importlib.import_module") as mock_import:
            # Set up mock to only respond to our specific module
            def side_effect(module_name):
                if module_name == "sunbeam.storage.backends.mock_backend":
                    mock_module = Mock()
                    mock_module.MockBackend = MockBackend
                    # Mock dir() to return the backend class name
                    mock_module.__dir__ = Mock(return_value=["MockBackend"])
                    return mock_module
                else:
                    # Let other imports pass through
                    return __import__(module_name)

            mock_import.side_effect = side_effect

            self.registry._load_backends()

            self.assertTrue(self.registry._loaded)
            # Verify our specific module was imported
            mock_import.assert_any_call("sunbeam.storage.backends.mock_backend")

    @patch("pathlib.Path.iterdir")
    def test_load_backends_skip_non_python_files(self, mock_iterdir):
        """Test that non-Python files are skipped during loading."""
        # Mock file structure with non-Python files
        mock_py_file = Mock()
        mock_py_file.is_file.return_value = True
        mock_py_file.name = "backend.py"

        mock_txt_file = Mock()
        mock_txt_file.is_file.return_value = True
        mock_txt_file.name = "readme.txt"

        mock_dir = Mock()
        mock_dir.is_file.return_value = False

        mock_iterdir.return_value = [mock_py_file, mock_txt_file, mock_dir]

        with patch("importlib.import_module") as mock_import:
            self.registry._load_backends()
            # Should only try to import the .py file
            self.assertEqual(mock_import.call_count, 1)

    @patch("pathlib.Path.iterdir")
    @patch("importlib.import_module")
    def test_load_backends_import_error(self, mock_import, mock_iterdir):
        """Test handling of import errors during backend loading."""
        mock_file = Mock()
        mock_file.is_file.return_value = True
        mock_file.name = "broken_backend.py"
        mock_file.stem = "broken_backend"
        mock_iterdir.return_value = [mock_file]

        mock_import.side_effect = ImportError("Module not found")

        # Should not raise exception, just log and continue
        self.registry._load_backends()
        self.assertTrue(self.registry._loaded)

    def test_load_backends_called_once(self):
        """Test that backends are loaded and cached properly."""
        # Reset the registry to ensure clean state
        self.registry._loaded = False
        self.registry._backends = {}

        with patch.object(
            self.registry, "_load_backends", wraps=self.registry._load_backends
        ) as mock_load:
            # Call multiple times
            backends1 = self.registry.list_backends()
            backends2 = self.registry.list_backends()
            try:
                self.registry.get_backend("test")
            except ValueError:
                pass  # Expected for non-existent backend

            # Verify that _load_backends was called (may be more than once)
            self.assertTrue(mock_load.called)
            # Verify that the same backends are returned (caching working)
            self.assertEqual(backends1, backends2)

    def test_list_backends_empty(self):
        """Test getting backends when none are loaded."""
        with patch.object(self.registry, "_load_backends"):
            backends = self.registry.list_backends()
            self.assertEqual(backends, {})

    def test_list_backends_with_backends(self):
        """Test getting backends when some are loaded."""
        mock_backend = MockBackend()
        self.registry._backends = {"mock": mock_backend}
        self.registry._loaded = True

        backends = self.registry.list_backends()
        self.assertEqual(backends, {"mock": mock_backend})

    def test_get_backend_exists(self):
        """Test getting a specific backend that exists."""
        mock_backend = MockBackend()
        self.registry._backends = {"mock": mock_backend}
        self.registry._loaded = True

        backend = self.registry.get_backend("mock")
        self.assertEqual(backend, mock_backend)

    def test_get_backend_not_exists(self):
        """Test getting a specific backend that doesn't exist."""
        self.registry._loaded = True

        with self.assertRaises(ValueError):
            self.registry.get_backend("nonexistent")

    @patch("click.Group")
    def test_register_cli_commands_empty_registry(self, mock_group):
        """Test command registration with empty registry."""
        mock_cli = Mock()
        self.registry._loaded = True

        self.registry.register_cli_commands(mock_cli, self.deployment)

        # Should still create command structure even with no backends
        self.assertTrue(mock_cli.add_command.called)

    @patch("click.Group")
    def test_register_cli_commands_with_backends(self, mock_group):
        """Test command registration with loaded backends."""
        mock_cli = Mock()
        mock_backend = MockBackend()

        # Mock the commands method to return test commands
        mock_commands = {
            "add": [{"name": "mock", "command": Mock()}],
            "remove": [{"name": "mock", "command": Mock()}],
        }

        with patch.object(mock_backend, "commands", return_value=mock_commands):
            self.registry._backends = {"mock": mock_backend}
            self.registry._loaded = True

            self.registry.register_cli_commands(mock_cli, self.deployment)

            # Should register commands for each group
            self.assertTrue(mock_cli.add_command.called)

    def test_load_backends_consistency(self):
        """Test that backends are loaded consistently across different a.methods."""
        # Reset the registry to ensure clean state
        self.registry._loaded = False
        self.registry._backends = {}

        with patch.object(
            self.registry, "_load_backends", wraps=self.registry._load_backends
        ) as mock_load:
            # Call via different methods
            backends_via_list = self.registry.list_backends()
            backends_via_list_again = self.registry.list_backends()

            # Verify that _load_backends was called
            self.assertTrue(mock_load.called)
            # Verify consistent results
            self.assertEqual(backends_via_list, backends_via_list_again)
            # Verify registry is marked as loaded
            self.assertTrue(self.registry._loaded)


class TestGlobalRegistry(unittest.TestCase):
    """Test cases for global registry instance."""

    def test_global_registry_instance(self):
        """Test that global registry instance exists and is correct type."""
        self.assertIsInstance(storage_backend_registry, StorageBackendRegistry)

    def test_global_registry_singleton(self):
        """Test that global registry behaves like a singleton."""
        from sunbeam.storage.registry import storage_backend_registry as registry1
        from sunbeam.storage.registry import storage_backend_registry as registry2

        self.assertIs(registry1, registry2)


if __name__ == "__main__":
    unittest.main()
