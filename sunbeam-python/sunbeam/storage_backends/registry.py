# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import importlib
import logging
import pathlib
from typing import Dict, List

import click
from rich.console import Console
from rich.table import Table

import sunbeam.storage_backends
from sunbeam.core.deployment import Deployment
from sunbeam.storage_backends.base import StorageBackendBase, StorageBackendInfo

LOG = logging.getLogger(__name__)
console = Console()

# Global registry for storage backends
_STORAGE_BACKENDS: Dict[str, StorageBackendBase] = {}


class StorageBackendRegistry:
    """Registry for managing storage backends following sunbeam patterns."""

    def __init__(self):
        self._backends: Dict[str, StorageBackendBase] = {}
        self._loaded = False

    def _load_backends(self) -> None:
        """Load all storage backends from the storage_backends directory."""
        if self._loaded:
            return

        LOG.debug("Loading storage backends")
        sunbeam_storage_backends = pathlib.Path(
            sunbeam.storage_backends.__file__
        ).parent

        for path in sunbeam_storage_backends.iterdir():
            if not path.is_file() or not path.name.endswith(".py"):
                continue
            if path.name.startswith("_") or path.name in ["base.py", "registry.py"]:
                continue

            module_name = path.stem
            try:
                LOG.debug(f"Loading storage backend module: {module_name}")
                mod = importlib.import_module(f"sunbeam.storage_backends.{module_name}")

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
                LOG.warning(f"Failed to load storage backend module {module_name}: {e}")

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

    def register_cli_commands(
        self, storage_group: click.Group, deployment: Deployment
    ) -> None:
        """Register all backend commands with the storage CLI group."""
        self._load_backends()

        # Create subgroups for add, remove, list
        add_group = click.Group("add", help="Add storage backends")
        remove_group = click.Group("remove", help="Remove storage backends")
        list_group = click.Group("list", help="List storage backends")

        # Add the general list command
        @list_group.command("all", help="List all storage backends")
        @click.option("--format", type=click.Choice(["table", "json"]), default="table")
        @click.pass_obj
        def list_all(deployment: Deployment, format: str):
            """List all deployed storage backends."""
            try:
                all_backends = []
                for backend in self._backends.values():
                    backend_list = backend.list_backends(deployment)
                    all_backends.extend(backend_list)

                if format == "json":
                    import json

                    console.print(
                        json.dumps([b.dict() for b in all_backends], indent=2)
                    )
                else:
                    self._display_backends_table(all_backends)

            except Exception as e:
                raise click.ClickException(str(e))

        # Register backend-specific commands
        for backend in self._backends.values():
            try:
                commands = backend.commands()

                # Register add commands
                if "storage.add" in commands:
                    for cmd_info in commands["storage.add"]:
                        add_group.add_command(cmd_info["command"])

                # Register remove commands
                if "storage.remove" in commands:
                    for cmd_info in commands["storage.remove"]:
                        remove_group.add_command(cmd_info["command"])

                # Register list commands
                if "storage.list" in commands:
                    for cmd_info in commands["storage.list"]:
                        list_group.add_command(cmd_info["command"])

            except Exception as e:
                LOG.warning(
                    f"Failed to register commands for backend {backend.name}: {e}"
                )

        # Add subgroups to main storage group
        storage_group.add_command(add_group)
        storage_group.add_command(remove_group)
        storage_group.add_command(list_group)

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


# Global registry instance
storage_backend_registry = StorageBackendRegistry()
