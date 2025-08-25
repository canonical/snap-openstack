# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import importlib
import logging
import pathlib
from typing import Dict, List

import click
from rich.console import Console
from rich.table import Table

from sunbeam.core.deployment import Deployment
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import StorageBackendInfo
from sunbeam.storage.service import StorageBackendService

LOG = logging.getLogger(__name__)
console = Console()

# Global registry for storage backends
_STORAGE_BACKENDS: Dict[str, StorageBackendBase] = {}


class StorageBackendRegistry:
    """Registry for managing storage backends."""

    def __init__(self):
        self._backends: Dict[str, StorageBackendBase] = {}
        self._loaded = False

    def _load_backends(self) -> None:
        """Load all storage backends from the storage/backends directory."""
        if self._loaded:
            return

        LOG.debug("Loading storage backends")
        import sunbeam.storage.backends

        sunbeam_storage_backends = pathlib.Path(
            sunbeam.storage.backends.__file__
        ).parent

        for path in sunbeam_storage_backends.iterdir():
            # Skip non-directories and special files
            if not path.is_dir() or path.name.startswith("_") or path.name == "etc":
                continue

            backend_name = path.name
            backend_module_path = path / "backend.py"

            # Check if the backend.py file exists in the backend directory
            if not backend_module_path.exists():
                LOG.debug(f"Skipping {backend_name}: no backend.py file found")
                continue

            try:
                LOG.debug(f"Loading storage backend: {backend_name}")
                # Import the backend module from the backend subdirectory
                mod = importlib.import_module(
                    f"sunbeam.storage.backends.{backend_name}.backend"
                )

                # Look for backend classes
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, StorageBackendBase)
                        and attr != StorageBackendBase
                    ):
                        backend_instance = attr()
                        self._backends[backend_instance.name] = backend_instance
                        LOG.debug(
                            f"Registered storage backend: {backend_instance.name}"
                        )

            except Exception as e:
                LOG.warning(f"Failed to load storage backend {backend_name}: {e}")

        self._loaded = True

    def get_backend(self, name: str) -> StorageBackendBase:
        """Get a storage backend by name."""
        self._load_backends()
        if name not in self._backends:
            raise ValueError(f"Storage backend '{name}' not found")
        return self._backends[name]

    def list_backends(self) -> Dict[str, StorageBackendBase]:
        """Get all available storage backends."""
        self._load_backends()
        return self._backends.copy()

    def get_backends(self) -> Dict[str, StorageBackendBase]:
        """Get all available storage backends (alias for list_backends)."""
        return self.list_backends()

    def register_cli_commands(
        self, storage_group: click.Group, deployment: Deployment
    ) -> None:
        """Register all backend commands with the storage CLI group.

        This now follows the provider pattern: create stable top-level groups
        and let each backend self-register its subcommands under those groups.
        The CLI UX remains the same, e.g.:
          sunbeam storage add <backend> [...]
          sunbeam storage remove <backend> <name>
          sunbeam storage list all
          sunbeam storage config show <backend> <name>
          sunbeam storage config set <backend> <name> key=value ...
          sunbeam storage config reset <backend> <name> key ...
          sunbeam storage config options <backend> [name]
        """
        self._load_backends()

        # Top-level subgroups
        add_group = click.Group(name="add")
        remove_group = click.Group(name="remove")
        list_group = click.Group(name="list")
        config_group = click.Group(name="config")

        # Config subgroups for backend-specific commands
        config_show = click.Group(name="show")
        config_set = click.Group(name="set")
        config_reset = click.Group(name="reset")
        config_options = click.Group(name="options")

        # Attach config subgroups to the config group
        config_group.add_command(config_show)
        config_group.add_command(config_set)
        config_group.add_command(config_reset)
        config_group.add_command(config_options)

        # List commands (keep generic 'all')
        @click.command(name="all")
        @click.pass_context
        def list_all(ctx):
            """List all storage backends."""
            service = StorageBackendService(deployment)
            backends = service.list_backends()
            self._display_backends_table(backends)

        list_group.add_command(list_all)

        # Delegate CLI registration to each backend
        for backend in self._backends.values():
            try:
                backend.register_add_cli(add_group)
                backend.register_cli(
                    remove_group,
                    config_show,
                    config_set,
                    config_options,
                    deployment,
                )
            except Exception as e:
                backend_name = getattr(backend, "name", "unknown")
                LOG.warning(
                    "Backend %s failed to register CLI: %s",
                    backend_name,
                    e,
                )

        # Mount groups under storage
        storage_group.add_command(add_group)
        storage_group.add_command(remove_group)
        storage_group.add_command(list_group)
        storage_group.add_command(config_group)

    def _display_backends_table(self, backends: List[StorageBackendInfo]) -> None:
        """Display backends in a formatted table."""
        if not backends:
            console.print("[yellow]No storage backends found[/yellow]")
            return

        table = Table(title="Storage Backends")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("Charm", style="blue")

        for backend in backends:
            status_style = "green" if backend.status == "active" else "red"
            table.add_row(
                backend.name,
                backend.backend_type,
                f"[{status_style}]{backend.status}[/{status_style}]",
                backend.charm,
            )

        console.print(table)
