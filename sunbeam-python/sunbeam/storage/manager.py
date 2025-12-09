# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import importlib
import logging
import pathlib
import typing
from typing import Dict

import click
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import StorageInstanceManifest
from sunbeam.errors import SunbeamException
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import BackendNotFoundException, StorageBackendInfo
from sunbeam.storage.service import StorageBackendService

LOG = logging.getLogger(__name__)
console = Console()

# Global registry for storage backends
_STORAGE_BACKENDS: Dict[str, StorageBackendBase] = {}


@click.group("storage", context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def storage(ctx):
    """Manage Cinder storage backends.

    Provides commands to add, remove, configure and list storage backends.
    Supports multiple backend types including Hitachi VSP and others.
    """
    # Ensure we have a deployment object
    if not hasattr(ctx, "obj") or not isinstance(ctx.obj, Deployment):
        raise click.ClickException(
            "Storage commands require a valid deployment context. "
            "Please ensure sunbeam is properly initialized."
        )


class StorageBackendManager:
    """Registry for managing storage backends."""

    _backends: dict[str, StorageBackendBase] = _STORAGE_BACKENDS
    _loaded: bool = False

    def __init__(self) -> None:
        if not self._backends:
            self._load_backends()

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
                        self._backends[backend_instance.backend_type] = backend_instance
                        LOG.debug(
                            "Registered storage backend: "
                            + backend_instance.backend_type
                        )

            except Exception as e:
                LOG.debug("Failed to load storage backend", exc_info=True)
                LOG.warning(f"Failed to load storage backend {backend_name}: {e}")

        self._loaded = True

    def get_backend(self, name: str) -> StorageBackendBase:
        """Get a storage backend by name."""
        self._load_backends()
        if name not in self._backends:
            raise ValueError(f"Storage backend '{name}' not found")
        return self._backends[name]

    def backends(self) -> typing.Mapping[str, StorageBackendBase]:
        """Get all available storage backends."""
        return self._backends

    def get_all_storage_manifests(
        self,
    ) -> dict[str, dict[str, StorageInstanceManifest]]:
        """Return a dict of all feature manifest defaults."""
        manifests: dict[str, dict[str, StorageInstanceManifest]] = {}
        for name in self.backends():
            manifests[name] = {}

        return manifests

    def register(self, cli: click.Group, deployment: Deployment) -> None:
        """Register storage backend commands with the storage group.

        This function is called from main.py to register all storage backend
        commands dynamically based on available backends.
        """
        cli.add_command(storage)
        try:
            self.register_cli_commands(storage, deployment)
            LOG.debug("Storage backend commands registered successfully")
        except Exception as e:
            LOG.error(f"Failed to register storage backend commands: {e}")
            raise e

    def register_cli_commands(  # noqa: C901
        self, storage_group: click.Group, deployment: Deployment
    ) -> None:
        """Register all backend commands with the storage CLI group.

        This follows the provider pattern: create stable top-level groups
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
        @click.group(name="add")
        def add_group():
            """Add a storage backend."""
            pass

        @click.group(name="options")
        def options_group():
            """Show storage backend configuration options."""
            pass

        @click.command(name="list")
        @click.pass_context
        def list_all(ctx):
            """List all storage backends."""
            jhelper = JujuHelper(deployment.juju_controller)
            service = StorageBackendService(deployment, jhelper)
            backends = service.list_backends()
            self._display_backends_table(backends)

        @click.command(name="remove")
        @click.argument("backend_name", type=str)
        @click.option("--force", is_flag=True, help="Skip confirmation prompt")
        @click.pass_context
        def remove_backend(ctx, backend_name: str, force: bool):
            """Remove a storage backend."""
            service = StorageBackendService(
                deployment, JujuHelper(deployment.juju_controller)
            )
            try:
                storage_backend = service.get_backend(backend_name)
            except BackendNotFoundException:
                console.print(
                    f"[red]Error: Storage backend {backend_name!r} not found.[/red]"
                )
                raise click.Abort()
            backend = self.backends().get(storage_backend.type)
            if not backend:
                console.print(
                    f"[red]Error: Storage backend type "
                    f"{storage_backend.type!r} not recognized.[/red]"
                )
                raise click.Abort()
            if not force:
                click.confirm(
                    f"Remove {backend.display_name} backend {backend_name!r}?",
                    abort=True,
                )
            try:
                backend.remove_backend(deployment, backend_name, console)
                console.print(
                    f"Successfully removed {backend.display_name} "
                    f"backend {backend_name!r}"
                )
            except Exception as e:
                console.print(f"[red]Error removing backend: {e}[/red]")
                raise click.Abort()

        @click.command(name="show")
        @click.argument("backend_name", type=str)
        @click.pass_context
        def show_backend(ctx, backend_name: str):
            """Show configuration for a storage backend."""
            service = StorageBackendService(
                deployment, JujuHelper(deployment.juju_controller)
            )
            try:
                storage_backend = service.get_backend(backend_name)
            except BackendNotFoundException:
                console.print(
                    f"[red]Error: Storage backend {backend_name!r} not found.[/red]"
                )
                raise click.Abort()
            backend = self.backends().get(storage_backend.type)
            if not backend:
                console.print(
                    f"[red]Error: Storage backend type "
                    f"{storage_backend.type!r} not recognized.[/red]"
                )
                raise click.Abort()
            config = backend.config_type().model_validate(
                storage_backend.config, by_alias=True
            )
            backend.display_config_table(backend_name, config)

        snap = Snap()
        # Delegate CLI registration to each backend
        try:
            client = deployment.get_client()
        except SunbeamException:
            # Might be called before bootstrap
            LOG.debug("Could not get client for deployment", exc_info=True)
            client = None
        for backend in self._backends.values():
            if not backend.is_enabled(client, snap):
                LOG.debug(
                    "Not registering backend %r, it is not enabled",
                    backend.backend_type,
                )
                continue
            try:
                backend.register_add_cli(add_group)
                backend.register_options_cli(options_group)
            except Exception as e:
                backend_name = getattr(backend, "name", "unknown")
                LOG.warning(
                    "Backend %s failed to register CLI: %s",
                    backend_name,
                    e,
                )
                raise

        # Mount groups under storage
        storage_group.add_command(list_all)
        storage_group.add_command(add_group)
        storage_group.add_command(show_backend)
        storage_group.add_command(options_group)
        storage_group.add_command(remove_backend)

    def _display_backends_table(self, backends: list[StorageBackendInfo]) -> None:
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
