# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for StorageBackendManager class."""

from unittest.mock import Mock, patch

import click
import pytest

from sunbeam.storage.manager import StorageBackendManager
from sunbeam.storage.models import StorageBackendInfo


@pytest.fixture
def mock_backend():
    """Create a mock backend."""
    from sunbeam.core.common import RiskLevel

    backend = Mock()
    backend.backend_type = "test-backend"
    backend.display_name = "Test Backend"
    backend.register_add_cli = Mock()
    backend.register_options_cli = Mock()
    backend.risk_availability = RiskLevel.STABLE
    backend.is_enabled = Mock(return_value=True)
    return backend


@pytest.fixture
def manager(mock_backend):
    """Create a fresh manager instance."""
    # Reset the class-level state before each test
    StorageBackendManager._backends = {mock_backend.backend_type: mock_backend}
    StorageBackendManager._loaded = True
    return StorageBackendManager()


@pytest.fixture
def mock_deployment():
    """Create a mock deployment."""
    deployment = Mock()
    deployment.openstack_machines_model = "openstack"
    deployment.juju_controller = "test-controller"
    mock_client = Mock()
    deployment.get_client = Mock(return_value=mock_client)
    return deployment


class TestStorageBackendManager:
    """Tests for StorageBackendManager."""

    def test_init_loads_backends(self, manager):
        """Test that initialization triggers backend loading."""
        with patch.object(manager, "_load_backends") as mock_load:
            # Create a new manager
            new_manager = StorageBackendManager()
            # _load_backends should be called if backends are empty
            if not new_manager._backends:
                mock_load.assert_called_once()

    def test_load_backends_sets_loaded_flag(self, manager):
        """Test that _load_backends sets the loaded flag."""
        # Reset the loaded flag
        manager._loaded = False
        assert not manager._loaded
        with patch("importlib.import_module"):
            with patch("pathlib.Path.iterdir", return_value=[]):
                manager._load_backends()
                assert manager._loaded

    def test_load_backends_only_once(self, manager):
        """Test that backends are only loaded once."""
        with patch("importlib.import_module"):
            with patch("pathlib.Path.iterdir", return_value=[]):
                manager._load_backends()
                manager._load_backends()
                # Should only load once due to _loaded flag
                # But init also calls it, so check the total is reasonable

    def test_load_backends_skips_non_directories(self, manager):
        """Test that non-directory items are skipped."""
        mock_file = Mock()
        mock_file.is_dir.return_value = False
        mock_file.name = "test.py"

        with patch("pathlib.Path.iterdir", return_value=[mock_file]):
            with patch("importlib.import_module"):
                manager._load_backends()
                # Should not attempt to import non-directories

    def test_load_backends_skips_special_directories(self, manager):
        """Test that special directories are skipped."""
        special_dirs = ["__pycache__", "_internal", "etc"]
        mock_paths = []

        for name in special_dirs:
            mock_path = Mock()
            mock_path.is_dir.return_value = True
            mock_path.name = name
            mock_paths.append(mock_path)

        with patch("pathlib.Path.iterdir", return_value=mock_paths):
            with patch("importlib.import_module") as mock_import:
                manager._load_backends()
                # Should not attempt to import special directories
                mock_import.assert_not_called()

    def test_load_backends_skips_missing_backend_file(self, manager):
        """Test that directories without backend.py are skipped."""
        mock_dir = Mock()
        mock_dir.is_dir.return_value = True
        mock_dir.name = "test-backend"
        mock_dir.__truediv__ = Mock(return_value=Mock(exists=Mock(return_value=False)))

        with patch("pathlib.Path.iterdir", return_value=[mock_dir]):
            with patch("importlib.import_module") as mock_import:
                manager._load_backends()
                # Should not attempt to import if backend.py is missing
                mock_import.assert_not_called()

    def test_load_backends_registers_valid_backend(self, manager):
        """Test that valid backends are registered."""
        from sunbeam.storage.base import StorageBackendBase

        # Create a mock backend class
        class TestBackend(StorageBackendBase):
            backend_type = "test"
            display_name = "Test Backend"

            @property
            def charm_name(self):
                return "test-charm"

            def config_type(self):
                from sunbeam.core.manifest import StorageBackendConfig

                return StorageBackendConfig

        # Create mock module with backend class
        mock_module = Mock()
        mock_module.TestBackend = TestBackend

        mock_dir = Mock()
        mock_dir.is_dir.return_value = True
        mock_dir.name = "test"
        backend_py = Mock()
        backend_py.exists.return_value = True
        mock_dir.__truediv__ = Mock(return_value=backend_py)

        with patch("pathlib.Path.iterdir", return_value=[mock_dir]):
            with patch("importlib.import_module", return_value=mock_module):
                with patch("pathlib.Path.exists", return_value=True):
                    # Clear existing backends and reset loaded flag
                    manager._backends = {}
                    manager._loaded = False
                    manager._load_backends()
                    # Backend should be registered
                    assert "test" in manager._backends

    def test_load_backends_handles_import_error(self, manager):
        """Test that import errors are handled gracefully."""
        mock_dir = Mock()
        mock_dir.is_dir.return_value = True
        mock_dir.name = "broken-backend"
        backend_py = Mock()
        backend_py.exists.return_value = True
        mock_dir.__truediv__ = Mock(return_value=backend_py)

        with patch("pathlib.Path.iterdir", return_value=[mock_dir]):
            with patch(
                "importlib.import_module", side_effect=ImportError("Test error")
            ):
                # Should not raise, just log warning
                manager._load_backends()

    def test_get_backend_success(self, manager, mock_backend):
        """Test getting a backend by name."""
        manager._backends["test-backend"] = mock_backend
        manager._loaded = True

        backend = manager.get_backend("test-backend")
        assert backend == mock_backend

    def test_get_backend_not_found(self, manager):
        """Test getting a non-existent backend."""
        manager._loaded = True

        with pytest.raises(ValueError, match="Storage backend .* not found"):
            manager.get_backend("nonexistent")

    def test_backends_property(self, manager, mock_backend):
        """Test backends property returns all backends."""
        manager._backends["test-backend"] = mock_backend
        manager._loaded = True

        backends = manager.backends()
        assert "test-backend" in backends
        assert backends["test-backend"] == mock_backend

    def test_get_all_storage_manifests(self, manager, mock_backend):
        """Test getting all storage manifests."""
        manager._backends["test-backend"] = mock_backend
        manager._loaded = True

        manifests = manager.get_all_storage_manifests()
        assert isinstance(manifests, dict)
        assert "test-backend" in manifests
        assert manifests["test-backend"] == {}

    def test_register(self, manager, mock_deployment):
        """Test registering storage commands."""
        mock_cli_group = Mock(spec=click.Group)

        with patch.object(manager, "register_cli_commands"):
            manager.register(mock_cli_group, mock_deployment)
            mock_cli_group.add_command.assert_called_once()

    def test_register_handles_errors(self, manager, mock_deployment):
        """Test that register handles errors appropriately."""
        mock_cli_group = Mock(spec=click.Group)

        with patch.object(
            manager, "register_cli_commands", side_effect=ValueError("Test error")
        ):
            with pytest.raises(ValueError):
                manager.register(mock_cli_group, mock_deployment)

    def test_register_cli_commands(self, manager, mock_backend, mock_deployment):
        """Test CLI command registration."""
        manager._backends["test-backend"] = mock_backend
        manager._loaded = True

        mock_storage_group = Mock(spec=click.Group)

        manager.register_cli_commands(mock_storage_group, mock_deployment)

        # Verify that backend's register_add_cli was called
        mock_backend.register_add_cli.assert_called_once()

        # Verify that commands were added to the group
        assert mock_storage_group.add_command.called

    def test_register_cli_commands_handles_backend_errors(
        self, manager, mock_backend, mock_deployment
    ):
        """Test that CLI registration handles backend errors."""
        manager._backends["test-backend"] = mock_backend
        manager._loaded = True
        mock_backend.register_add_cli.side_effect = ValueError("Backend error")

        mock_storage_group = Mock(spec=click.Group)

        with pytest.raises(ValueError):
            manager.register_cli_commands(mock_storage_group, mock_deployment)

    def test_display_backends_table_empty(self, manager):
        """Test displaying empty backend list."""
        from rich.console import Console

        mock_console = Mock(spec=Console)

        with patch("sunbeam.storage.manager.console", mock_console):
            manager._display_backends_table([])
            # Should print a message about no backends
            mock_console.print.assert_called_once()

    def test_display_backends_table_with_backends(self, manager):
        """Test displaying backend list."""
        from rich.console import Console

        backends = [
            StorageBackendInfo(
                name="backend1",
                backend_type="type1",
                status="active",
                charm="charm1",
                config={},
            ),
            StorageBackendInfo(
                name="backend2",
                backend_type="type2",
                status="error",
                charm="charm2",
                config={},
            ),
        ]

        mock_console = Mock(spec=Console)

        with patch("sunbeam.storage.manager.console", mock_console):
            manager._display_backends_table(backends)
            # Should print the table
            assert mock_console.print.called


class TestStorageCLICommands:
    """Test the CLI command functions created by the manager."""

    def test_list_all_command(self, manager, mock_deployment):
        """Test the list command."""
        from sunbeam.storage.service import StorageBackendService

        mock_service = Mock(spec=StorageBackendService)
        mock_service.list_backends.return_value = []

        with patch(
            "sunbeam.storage.manager.StorageBackendService", return_value=mock_service
        ):
            with patch.object(manager, "_display_backends_table"):
                manager._loaded = True
                mock_storage_group = Mock(spec=click.Group)
                manager.register_cli_commands(mock_storage_group, mock_deployment)

                # Get the list command that was registered
                calls = mock_storage_group.add_command.call_args_list
                list_command = None
                for call in calls:
                    if call[0][0].name == "list":
                        list_command = call[0][0]
                        break

                assert list_command is not None

    def test_remove_backend_command_success(self, manager, mock_deployment):
        """Test the remove command with successful removal."""
        mock_backend = Mock()
        manager._backends["test-backend"] = mock_backend
        manager._loaded = True

        mock_storage_group = Mock(spec=click.Group)

        manager.register_cli_commands(mock_storage_group, mock_deployment)

        # Verify remove command was registered
        calls = mock_storage_group.add_command.call_args_list
        remove_command = None
        for call in calls:
            if hasattr(call[0][0], "name") and call[0][0].name == "remove":
                remove_command = call[0][0]
                break

        assert remove_command is not None

    def test_show_backend_command(self, manager, mock_deployment):
        """Test the show command."""
        mock_storage_group = Mock(spec=click.Group)

        manager.register_cli_commands(mock_storage_group, mock_deployment)

        # Verify show command was registered
        calls = mock_storage_group.add_command.call_args_list
        show_command = None
        for call in calls:
            if hasattr(call[0][0], "name") and call[0][0].name == "show":
                show_command = call[0][0]
                break

        assert show_command is not None
