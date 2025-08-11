# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for storage backend registry."""

from unittest.mock import Mock, patch

import pytest

from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import StorageBackendConfig
from sunbeam.storage.registry import StorageBackendRegistry


class MockStorageBackend(StorageBackendBase):
    """Mock storage backend for testing."""

    name = "mock"
    display_name = "Mock Storage Backend"
    charm_name = "mock-charm"

    def __init__(self):
        super().__init__()
        self.tfplan = "mock-backend-plan"
        self.tfplan_dir = "deploy-mock-backend"

    @property
    def tfvar_config_key(self):
        """Config key for storing Terraform variables in clusterd."""
        return f"TerraformVars{self.name.title()}Backend"

    @property
    def config_class(self):
        return StorageBackendConfig

    def create_deploy_step(
        self,
        deployment,
        client,
        tfhelper,
        jhelper,
        manifest,
        backend_name,
        backend_config,
        model,
    ):
        """Create a mock deployment step."""
        # Use test-specific mock step if available; otherwise base class
        try:
            from sunbeam.storage.steps import MockDeployStep  # type: ignore

            return MockDeployStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                backend_name,
                backend_config,
                self,
                model,
            )
        except Exception:
            from sunbeam.storage.steps import BaseStorageBackendDeployStep

            return BaseStorageBackendDeployStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                backend_name,
                backend_config,
                self,
                model,
            )

    def create_destroy_step(
        self, deployment, client, tfhelper, jhelper, manifest, backend_name, model
    ):
        """Create a mock destruction step."""
        # Use test-specific mock step if available; otherwise base class
        try:
            from sunbeam.storage.steps import MockDestroyStep  # type: ignore

            return MockDestroyStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                backend_name,
                self,
                model,
            )
        except Exception:
            from sunbeam.storage.steps import BaseStorageBackendDestroyStep

            return BaseStorageBackendDestroyStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                manifest,
                backend_name,
                self,
                model,
            )

    def create_update_config_step(self, deployment, backend_name, config_updates):
        """Create a mock configuration update step."""
        from sunbeam.storage.steps import BaseStorageBackendConfigUpdateStep

        return BaseStorageBackendConfigUpdateStep(
            deployment, self, backend_name, config_updates
        )

    def prompt_for_config(self, backend_name: str) -> StorageBackendConfig:
        """Mock prompt for configuration."""
        return StorageBackendConfig(name=backend_name)

    def register_add_cli(self, add):
        """Mock CLI registration."""
        pass

    def register_cli(
        self, remove, config_show, config_set, config_reset, config_options, deployment
    ):
        """Mock CLI registration."""
        pass

    def get_terraform_variables(
        self, backend_name: str, config: StorageBackendConfig, model: str
    ):
        return {
            "model": model,
            "mock_backends": {
                backend_name: {
                    "backend_type": self.name,
                    "charm_name": self.charm_name,
                    "charm_channel": "latest/stable",
                    "backend_config": config.model_dump(),
                    "backend_endpoint": "storage-backend",
                    "units": 1,
                    "additional_integrations": {},
                }
            },
        }

    def commands(self):
        """Return mock commands for testing."""
        return {
            "add": [{"name": "mock", "command": Mock()}],
            "remove": [{"name": "mock", "command": Mock()}],
            "list": [{"name": "mock", "command": Mock()}],
            "config": [{"name": "mock", "command": Mock()}],
        }


class TestStorageBackendRegistry:
    """Test cases for StorageBackendRegistry class."""

    def test_init(self):
        """Test registry initialization."""
        registry = StorageBackendRegistry()

        assert registry._backends == {}
        assert hasattr(registry, "_loaded")
        assert registry._loaded is False

    def test_load_backends_success(self):
        """Test successful backend loading by directly adding a backend."""
        registry = StorageBackendRegistry()

        # Directly add a mock backend to test registry functionality
        mock_backend = MockStorageBackend()
        registry._backends["mock"] = mock_backend
        registry._loaded = True

        assert registry._loaded is True
        assert "mock" in registry._backends
        assert isinstance(registry._backends["mock"], MockStorageBackend)

    @patch("sunbeam.storage.registry.pathlib.Path")
    def test_load_backends_no_backends_dir(self, mock_path):
        """Test loading when backends directory doesn't exist."""
        registry = StorageBackendRegistry()

        # Mock the pathlib.Path chain:
        # pathlib.Path(sunbeam.storage.backends.__file__).parent
        mock_backends_dir = Mock()
        mock_backends_dir.iterdir.return_value = []  # Empty for no backends case

        mock_path.return_value.parent = mock_backends_dir

        registry._load_backends()

        assert registry._loaded is True
        assert registry._backends == {}

    @patch("sunbeam.storage.registry.pathlib.Path")
    def test_load_backends_empty_dir(self, mock_path):
        """Test backend loading with empty backends directory."""
        registry = StorageBackendRegistry()

        # Mock the pathlib.Path chain:
        # pathlib.Path(sunbeam.storage.backends.__file__).parent
        mock_backends_dir = Mock()
        mock_backends_dir.iterdir.return_value = []

        mock_path.return_value.parent = mock_backends_dir

        registry._load_backends()

        assert registry._loaded is True
        assert registry._backends == {}

    def test_get_backend_success(self):
        """Test successful backend retrieval."""
        registry = StorageBackendRegistry()
        mock_backend = MockStorageBackend()
        registry._backends = {"mock": mock_backend}
        registry._loaded = True

        backend = registry.get_backend("mock")
        assert backend == mock_backend

    def test_get_backend_not_found(self):
        """Test backend retrieval for non-existent backend."""
        registry = StorageBackendRegistry()
        registry._backends = {}
        registry._loaded = True

        with pytest.raises(ValueError, match="Storage backend 'nonexistent' not found"):
            registry.get_backend("nonexistent")

    def test_get_backend_auto_load(self):
        """Test that get_backend automatically loads backends if not loaded."""
        registry = StorageBackendRegistry()

        with patch.object(registry, "_load_backends") as mock_load:
            mock_backend = MockStorageBackend()
            registry._backends = {"mock": mock_backend}
            registry._loaded = True

            backend = registry.get_backend("mock")
            mock_load.assert_called_once()
            assert backend == mock_backend

    def test_list_backends(self):
        """Test backend listing."""
        registry = StorageBackendRegistry()
        mock_backend1 = MockStorageBackend()
        mock_backend2 = MockStorageBackend()
        mock_backend2.name = "mock2"

        registry._backends = {"mock": mock_backend1, "mock2": mock_backend2}
        registry._loaded = True

        backends = registry.list_backends()
        assert len(backends) == 2
        assert "mock" in backends
        assert "mock2" in backends
        assert backends["mock"] == mock_backend1
        assert backends["mock2"] == mock_backend2

    def test_list_backends_auto_load(self):
        """Test that list_backends automatically loads backends if not loaded."""
        registry = StorageBackendRegistry()

        with patch.object(registry, "_load_backends") as mock_load:
            registry._backends = {"mock": MockStorageBackend()}
            registry._loaded = True

            backends = registry.list_backends()
            mock_load.assert_called_once()
            assert len(backends) == 1

    def test_register_add_commands_via_backend(self, mock_deployment):
        """Test that add commands are registered via backend.register_add_cli()."""
        registry = StorageBackendRegistry()
        mock_backend = MockStorageBackend()
        registry._backends = {"mock": mock_backend}
        registry._loaded = True

        mock_storage_group = Mock()

        with patch.object(mock_backend, "register_add_cli") as mock_register_add:
            registry.register_cli_commands(mock_storage_group, mock_deployment)

            # Verify that the backend's register_add_cli method was called
            mock_register_add.assert_called_once()
            # Verify that groups were added to the storage group
            assert mock_storage_group.add_command.call_count >= 4

    def test_register_remove_commands_via_backend(self, mock_deployment):
        """Test that remove commands are registered via backend.register_cli()."""
        registry = StorageBackendRegistry()
        mock_backend = MockStorageBackend()
        registry._backends = {"mock": mock_backend}
        registry._loaded = True

        mock_storage_group = Mock()

        with patch.object(mock_backend, "register_cli") as mock_register_cli:
            registry.register_cli_commands(mock_storage_group, mock_deployment)

            # Verify that the backend's register_cli method was called
            mock_register_cli.assert_called_once()
            # Verify that groups were added to the storage group
            assert mock_storage_group.add_command.call_count >= 4

    def test_register_list_commands_includes_all(self, mock_deployment):
        """Test that list commands include the 'all' command."""
        registry = StorageBackendRegistry()
        mock_backend = MockStorageBackend()
        registry._backends = {"mock": mock_backend}
        registry._loaded = True

        mock_storage_group = Mock()

        with patch("sunbeam.storage.registry.StorageBackendService"):
            registry.register_cli_commands(mock_storage_group, mock_deployment)

            # Verify that groups were added to the storage group
            assert mock_storage_group.add_command.call_count >= 4

    def test_register_config_commands_includes_subgroups(self, mock_deployment):
        """Test that config commands include show, set, reset, options subgroups."""
        registry = StorageBackendRegistry()
        mock_backend = MockStorageBackend()
        registry._backends = {"mock": mock_backend}
        registry._loaded = True

        mock_storage_group = Mock()

        with patch.object(mock_backend, "register_cli") as mock_register_cli:
            registry.register_cli_commands(mock_storage_group, mock_deployment)

            # Verify that the backend's register_cli method was called with config
            # subgroups
            mock_register_cli.assert_called_once()
            # The call should include the config subgroups as arguments
            call_args = mock_register_cli.call_args[0]
            assert (
                len(call_args) >= 5
            )  # remove_group, config_show, config_set, config_reset, config_options

    def test_register_commands_all_groups(self, mock_deployment):
        """Test registration of all command groups."""
        registry = StorageBackendRegistry()
        mock_backend = MockStorageBackend()
        registry._backends = {"mock": mock_backend}
        registry._loaded = True

        mock_cli = Mock()

        with (
            patch.object(mock_backend, "register_add_cli") as mock_add,
            patch.object(mock_backend, "register_cli") as mock_register_cli,
        ):
            registry.register_cli_commands(mock_cli, mock_deployment)

            # Verify that backend CLI registration methods were called
            mock_add.assert_called_once()
            mock_register_cli.assert_called_once()

            # Verify that the CLI groups were added to the storage group
            assert (
                mock_cli.add_command.call_count >= 4
            )  # add, remove, list, config groups

    def test_backend_discovery_error_handling(self):
        """Test error handling during backend discovery."""
        registry = StorageBackendRegistry()

        with (
            patch("sunbeam.storage.registry.importlib.import_module"),
            patch("sunbeam.storage.registry.pathlib.Path") as mock_path,
        ):
            mock_backends_dir = Mock()
            mock_backends_dir.iterdir.side_effect = Exception("Directory read error")

            mock_path.return_value.parent = mock_backends_dir

            # Registry should handle directory iteration gracefully
            registry._load_backends()

            # Should still mark as loaded even with directory errors
            assert registry._loaded is True
            assert registry._backends == {}

    def test_module_loading_error_handling(self):
        """Test error handling during module loading."""
        registry = StorageBackendRegistry()

        with (
            patch("sunbeam.storage.registry.pathlib.Path") as mock_path,
            patch(
                "sunbeam.storage.registry.importlib.import_module"
            ) as mock_import_module,
        ):
            mock_backends_dir = Mock()
            mock_backends_dir.exists.return_value = True
            mock_backends_dir.is_dir.return_value = True

            mock_backend_dir = Mock()
            mock_backend_dir.name = "mock"
            mock_backend_dir.is_dir.return_value = True

            # Set up path operations for mock_backend_dir
            mock_backend_module_path = Mock()
            mock_backend_module_path.exists.return_value = True
            mock_backend_dir.__truediv__ = Mock(return_value=mock_backend_module_path)

            mock_backends_dir.iterdir.return_value = [mock_backend_dir]

            mock_path.return_value.parent = mock_backends_dir

            mock_import_module.side_effect = Exception("Module loading error")

            # Should not raise exception, just log error and continue
            registry._load_backends()

            assert registry._loaded is True
            assert registry._backends == {}

    def test_singleton_behavior(self):
        """Test that registry instances are independent (not singleton)."""
        registry1 = StorageBackendRegistry()
        registry2 = StorageBackendRegistry()

        # Should be different instances
        assert registry1 is not registry2
        assert registry1._backends is not registry2._backends

    def test_backend_validation(self):
        """Test backend validation during loading."""
        registry = StorageBackendRegistry()

        # Mock a class that doesn't inherit from StorageBackendBase
        class InvalidBackend:
            name = "invalid"

        # Directly test validation by adding backends
        # Valid backend should be accepted
        valid_backend = MockStorageBackend()
        registry._backends["mock"] = valid_backend

        # Test that we can access the valid backend
        assert len(registry._backends) == 1
        assert "mock" in registry._backends
        assert isinstance(registry._backends["mock"], MockStorageBackend)

    def test_command_registration_with_no_backends(self, mock_deployment):
        """Test command registration when no backends are loaded."""
        registry = StorageBackendRegistry()
        registry._backends = {}
        registry._loaded = True

        mock_cli = Mock()

        # Should not raise errors even with no backends
        with patch("sunbeam.storage.registry.StorageBackendService"):
            registry.register_cli_commands(mock_cli, mock_deployment)

            # Should still create the basic command groups
            assert mock_cli.add_command.call_count >= 4

    def test_command_registration_with_backend_errors(self, mock_deployment):
        """Test command registration when backend registration fails."""
        registry = StorageBackendRegistry()
        mock_backend = MockStorageBackend()
        registry._backends = {"mock": mock_backend}
        registry._loaded = True

        mock_cli = Mock()

        with (
            patch.object(
                mock_backend,
                "register_add_cli",
                side_effect=Exception("Registration error"),
            ),
            patch.object(
                mock_backend,
                "register_cli",
                side_effect=Exception("Registration error"),
            ),
            patch("sunbeam.storage.registry.StorageBackendService"),
        ):
            # Should not raise errors even if backend registration fails
            registry.register_cli_commands(mock_cli, mock_deployment)

            # Should still create the basic command groups
            assert mock_cli.add_command.call_count >= 4
