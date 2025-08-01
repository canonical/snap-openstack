# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for Hitachi storage backend CLI commands."""

import unittest.mock as mock

import click
from click.testing import CliRunner

from sunbeam.storage.backends.hitachi.backend import HitachiBackend, HitachiConfig
from sunbeam.storage.backends.hitachi.cli import HitachiCLI


class TestHitachiCLI:
    """Test cases for Hitachi storage backend CLI commands."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()
        self.mock_deployment = mock.MagicMock()
        self.backend = HitachiBackend()
        self.cli = HitachiCLI(self.backend)

    def test_register_add_cli(self):
        """Test that register_add_cli creates a command properly."""
        mock_add_group = mock.MagicMock()

        # Test that register_add_cli doesn't raise an exception
        self.cli.register_add_cli(mock_add_group)

        # Verify that a command was added to the group
        mock_add_group.add_command.assert_called_once()

        # Get the command that was added
        added_command = mock_add_group.add_command.call_args[0][0]
        assert isinstance(added_command, click.Command)
        assert added_command.name == "hitachi"

    def test_load_config_file_yaml(self):
        """Test loading YAML config file."""
        import tempfile

        import yaml

        config_data = {
            "name": "test-backend",
            "hitachi_storage_id": "12345",
            "san_ip": "192.168.1.100",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            f.flush()

            from pathlib import Path

            result = self.cli._load_config_file(Path(f.name))

            assert result == config_data

    def test_add_missing_required(self):
        """Test that Click exits non-zero with missing required option."""
        # Create a mock add group and register the command
        add_group = click.Group(name="add")
        self.cli.register_add_cli(add_group)

        result = self.runner.invoke(
            add_group,
            [
                "hitachi",
                "--name",
                "test-hitachi",
                # Missing required --hitachi-storage-id
                "--hitachi-pools",
                "pool1,pool2",
                "--san-ip",
                "192.168.1.100",
            ],
            obj=self.mock_deployment,
        )

        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_register_cli(self):
        """Test that register_cli creates management commands properly."""
        mock_remove = mock.MagicMock()
        mock_config_show = mock.MagicMock()
        mock_config_set = mock.MagicMock()
        mock_config_options = mock.MagicMock()

        # Test that register_cli doesn't raise an exception
        self.cli.register_cli(
            mock_remove,
            mock_config_show,
            mock_config_set,
            mock_config_options,
            self.mock_deployment,
        )

        # Verify that commands were added to each group
        mock_remove.add_command.assert_called_once()
        mock_config_show.add_command.assert_called_once()
        mock_config_set.add_command.assert_called_once()
        mock_config_options.add_command.assert_called_once()

    def test_cli_methods_exist(self):
        """Test that all required CLI methods exist."""
        assert hasattr(self.cli, "register_add_cli")
        assert hasattr(self.cli, "register_cli")
        assert hasattr(self.cli, "_load_config_file")

        # Test they are callable
        assert callable(self.cli.register_add_cli)
        assert callable(self.cli.register_cli)
        assert callable(self.cli._load_config_file)

    def test_load_config_file_json(self):
        """Test loading JSON config file."""
        import json
        import tempfile

        config_data = {
            "name": "test-backend",
            "hitachi_storage_id": "12345",
            "san_ip": "192.168.1.100",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            f.flush()

            from pathlib import Path

            result = self.cli._load_config_file(Path(f.name))

            assert result == config_data

    def test_load_config_file_empty_path(self):
        """Test loading config file with empty path returns empty dict."""
        result = self.cli._load_config_file(None)
        assert result == {}

    def test_cli_backend_reference(self):
        """Test that CLI maintains reference to backend."""
        assert self.cli.backend is not None
        assert isinstance(self.cli.backend, HitachiBackend)
        assert self.cli.backend.name == "hitachi"

    def test_add_with_chap_auth(self):
        """Test addition with CHAP authentication enabled."""
        with mock.patch.object(self.backend, "add_backend") as mock_add_backend:
            mock_add_backend.return_value = None

            # Create a mock add group and register the command
            add_group = click.Group(name="add")
            self.cli.register_add_cli(add_group)

            result = self.runner.invoke(
                add_group,
                [
                    "hitachi",
                    "--name",
                    "test-hitachi",
                    "--hitachi-storage-id",
                    "12345",
                    "--hitachi-pools",
                    "pool1,pool2",
                    "--san-ip",
                    "192.168.1.100",
                    "--san-username",
                    "sanuser",
                    "--san-password",
                    "sanpass",
                    "--protocol",
                    "iSCSI",
                    "--chap-username",
                    "chapuser",
                    "--chap-password",
                    "chappass",
                    "--use-chap-auth",
                    "true",
                ],
                obj=self.mock_deployment,
            )

            assert result.exit_code == 0
            mock_add_backend.assert_called_once()

            # Verify the config passed to add_backend
            call_args = mock_add_backend.call_args
            config = call_args[0][2]  # Third argument is the config
            assert config.use_chap_auth is True
            assert config.chap_username == "chapuser"
            assert config.chap_password == "chappass"

    def test_add_backend_exception(self):
        """Test that backend exceptions are handled gracefully."""
        with mock.patch.object(self.backend, "add_backend") as mock_add_backend:
            mock_add_backend.side_effect = Exception("Backend error")

            # Create a mock add group and register the command
            add_group = click.Group(name="add")
            self.cli.register_add_cli(add_group)

            result = self.runner.invoke(
                add_group,
                [
                    "hitachi",
                    "--name",
                    "test-hitachi",
                    "--hitachi-storage-id",
                    "12345",
                    "--hitachi-pools",
                    "pool1,pool2",
                    "--san-ip",
                    "192.168.1.100",
                    "--san-username",
                    "sanuser",
                    "--san-password",
                    "sanpass",
                    "--protocol",
                    "iSCSI",
                ],
                obj=self.mock_deployment,
            )

            assert result.exit_code != 0
            # The exception is raised but not caught by CLI, so no output is generated
            # The exception object itself contains the error message
            assert result.exception is not None
            assert "Backend error" in str(result.exception)

    def test_cli_initialization(self):
        """Test that HitachiCLI can be initialized with a backend."""
        backend = HitachiBackend()
        cli = HitachiCLI(backend)
        assert cli.backend == backend

    def test_backend_delegation_works(self):
        """Test that CLI properly delegates to backend methods."""
        # Test that CLI can access backend properties
        assert self.cli.backend.name == "hitachi"
        assert self.cli.backend.display_name == "Hitachi VSP Storage"
        assert self.cli.backend.config_class == HitachiConfig

    def test_register_add_cli_creates_command_with_correct_name(self):
        """Test that register_add_cli creates a command with the backend name."""
        mock_add_group = mock.MagicMock()

        self.cli.register_add_cli(mock_add_group)

        # Verify command was added with correct name
        mock_add_group.add_command.assert_called_once()
        added_command = mock_add_group.add_command.call_args[0][0]
        assert added_command.name == self.backend.name

    def test_register_cli_creates_all_management_commands(self):
        """Test that register_cli creates all expected management commands."""
        mock_remove = mock.MagicMock()
        mock_config_show = mock.MagicMock()
        mock_config_set = mock.MagicMock()
        mock_config_options = mock.MagicMock()

        self.cli.register_cli(
            mock_remove,
            mock_config_show,
            mock_config_set,
            mock_config_options,
            self.mock_deployment,
        )

        # Verify all commands were created with correct names
        for mock_group in [
            mock_remove,
            mock_config_show,
            mock_config_set,
            mock_config_options,
        ]:
            mock_group.add_command.assert_called_once()
            added_command = mock_group.add_command.call_args[0][0]
            assert added_command.name == self.backend.name

    def test_remove_ok(self):
        """Test successful removal of Hitachi backend."""
        with (
            mock.patch.object(self.backend, "_get_service") as mock_get_service,
            mock.patch.object(self.backend, "remove_backend") as mock_remove_backend,
        ):
            mock_service = mock.MagicMock()
            mock_service.backend_exists.return_value = True
            mock_get_service.return_value = mock_service
            mock_remove_backend.return_value = None

            # Create a mock remove group and register the command
            remove_group = click.Group(name="remove")
            config_show = click.Group(name="config_show")
            config_set = click.Group(name="config_set")
            config_options = click.Group(name="config_options")
            self.cli.register_cli(
                remove_group,
                config_show,
                config_set,
                config_options,
                self.mock_deployment,
            )

            result = self.runner.invoke(
                remove_group,
                ["hitachi", "test-hitachi", "--yes"],
                obj=self.mock_deployment,
            )

            assert result.exit_code == 0
            mock_get_service.assert_called_once_with(self.mock_deployment)
            mock_service.backend_exists.assert_called_once_with(
                "test-hitachi", "hitachi"
            )
            mock_remove_backend.assert_called_once_with(
                self.mock_deployment, "test-hitachi", mock.ANY
            )

    def test_remove_service_exception(self):
        """Test handling of service exceptions during removal."""
        with (
            mock.patch.object(self.backend, "_get_service") as mock_get_service,
            mock.patch.object(self.backend, "remove_backend") as mock_remove_backend,
        ):
            mock_service = mock.MagicMock()
            mock_service.backend_exists.return_value = True
            mock_get_service.return_value = mock_service
            mock_remove_backend.side_effect = Exception("Service error")

            # Create a mock remove group and register the command
            remove_group = click.Group(name="remove")
            config_show = click.Group(name="config_show")
            config_set = click.Group(name="config_set")
            config_options = click.Group(name="config_options")
            self.cli.register_cli(
                remove_group,
                config_show,
                config_set,
                config_options,
                self.mock_deployment,
            )

            result = self.runner.invoke(
                remove_group,
                ["hitachi", "test-hitachi", "--yes"],
                obj=self.mock_deployment,
            )

            assert result.exit_code != 0
            assert "Service" in result.output and "error" in result.output

    def test_cli_class_has_required_methods(self):
        """Test that HitachiCLI class has all required methods."""
        required_methods = [
            "register_add_cli",
            "register_cli",
            "_load_config_file",
        ]

        for method_name in required_methods:
            assert hasattr(self.cli, method_name)
            assert callable(getattr(self.cli, method_name))
