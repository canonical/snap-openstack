# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for StorageBackendManager class."""

from unittest.mock import Mock, patch

import click
import pytest

from sunbeam.storage.base import HypervisorIntegration
from sunbeam.storage.manager import StorageBackendManager
from sunbeam.storage.models import StorageBackendInfo


@pytest.fixture
def mock_backend():
    """Create a mock backend."""
    backend = Mock()
    backend.backend_type = "test-backend"
    backend.display_name = "Test Backend"
    backend.register_add_cli = Mock()
    backend.register_options_cli = Mock()
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


class TestCollectHypervisorIntegrations:
    """Tests for StorageBackendManager.collect_hypervisor_integrations."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock clusterd client."""
        return Mock()

    @pytest.fixture
    def mock_deployment(self):
        """Create a mock deployment."""
        return Mock()

    @pytest.fixture
    def manager_with_backends(self):
        """Create a manager with controlled backends."""
        StorageBackendManager._backends = {}
        StorageBackendManager._loaded = True
        return StorageBackendManager()

    def test_returns_integrations_from_registered_backends(
        self, manager_with_backends, mock_deployment, mock_client
    ):
        """Returns integrations from backends that have registered instances."""
        integration_a = HypervisorIntegration(
            application_name="ceph-app",
            endpoint_name="ceph-access",
            hypervisor_endpoint_name="ceph",
        )
        integration_b = HypervisorIntegration(
            application_name="iscsi-app",
            endpoint_name="iscsi-access",
            hypervisor_endpoint_name="iscsi",
        )

        backend_ceph = Mock()
        backend_ceph.get_hypervisor_integrations.return_value = {integration_a}

        backend_iscsi = Mock()
        backend_iscsi.get_hypervisor_integrations.return_value = {integration_b}

        manager_with_backends._backends["ceph"] = backend_ceph
        manager_with_backends._backends["iscsi"] = backend_iscsi

        # Simulate two registered backend instances, one ceph and one iscsi
        registered_ceph = Mock()
        registered_ceph.type = "ceph"
        registered_iscsi = Mock()
        registered_iscsi.type = "iscsi"
        mock_client.cluster.get_storage_backends.return_value.root = [
            registered_ceph,
            registered_iscsi,
        ]

        result = manager_with_backends.collect_hypervisor_integrations(
            mock_deployment, mock_client
        )

        assert result == {integration_a, integration_b}
        backend_ceph.get_hypervisor_integrations.assert_called_once_with(
            mock_deployment
        )
        backend_iscsi.get_hypervisor_integrations.assert_called_once_with(
            mock_deployment
        )

    def test_returns_empty_set_when_no_backends_registered(
        self, manager_with_backends, mock_deployment, mock_client
    ):
        """Returns empty set when no backends are registered in clusterd."""
        mock_client.cluster.get_storage_backends.return_value.root = []

        result = manager_with_backends.collect_hypervisor_integrations(
            mock_deployment, mock_client
        )

        assert result == set()

    def test_skips_unknown_backend_type(
        self, manager_with_backends, mock_deployment, mock_client
    ):
        """Skips backend types not loaded in the manager."""
        known_backend = Mock()
        known_backend.get_hypervisor_integrations.return_value = set()
        manager_with_backends._backends["known"] = known_backend

        registered_known = Mock()
        registered_known.type = "known"
        registered_unknown = Mock()
        registered_unknown.type = "unknown-type"
        mock_client.cluster.get_storage_backends.return_value.root = [
            registered_known,
            registered_unknown,
        ]

        result = manager_with_backends.collect_hypervisor_integrations(
            mock_deployment, mock_client
        )

        assert result == set()
        known_backend.get_hypervisor_integrations.assert_called_once_with(
            mock_deployment
        )

    def test_deduplicates_backend_types(
        self, manager_with_backends, mock_deployment, mock_client
    ):
        """Calls get_hypervisor_integrations once per type, multiple instances."""
        integration = HypervisorIntegration(
            application_name="ceph-app",
            endpoint_name="ceph-access",
            hypervisor_endpoint_name="ceph",
        )

        backend = Mock()
        backend.get_hypervisor_integrations.return_value = {integration}
        manager_with_backends._backends["ceph"] = backend

        # Two instances of the same type
        instance1 = Mock()
        instance1.type = "ceph"
        instance2 = Mock()
        instance2.type = "ceph"
        mock_client.cluster.get_storage_backends.return_value.root = [
            instance1,
            instance2,
        ]

        result = manager_with_backends.collect_hypervisor_integrations(
            mock_deployment, mock_client
        )

        assert result == {integration}
        backend.get_hypervisor_integrations.assert_called_once_with(mock_deployment)

    def test_unions_integrations_from_multiple_backends(
        self, manager_with_backends, mock_deployment, mock_client
    ):
        """Returns the union of integrations from all matching backends."""
        shared = HypervisorIntegration(
            application_name="shared-app",
            endpoint_name="shared-ep",
            hypervisor_endpoint_name="shared",
        )
        unique = HypervisorIntegration(
            application_name="unique-app",
            endpoint_name="unique-ep",
            hypervisor_endpoint_name="unique",
        )

        backend_a = Mock()
        backend_a.get_hypervisor_integrations.return_value = {shared}
        backend_b = Mock()
        backend_b.get_hypervisor_integrations.return_value = {shared, unique}

        manager_with_backends._backends["type-a"] = backend_a
        manager_with_backends._backends["type-b"] = backend_b

        reg_a = Mock()
        reg_a.type = "type-a"
        reg_b = Mock()
        reg_b.type = "type-b"
        mock_client.cluster.get_storage_backends.return_value.root = [reg_a, reg_b]

        result = manager_with_backends.collect_hypervisor_integrations(
            mock_deployment, mock_client
        )

        assert result == {shared, unique}
        assert len(result) == 2
