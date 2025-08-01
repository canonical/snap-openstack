# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import importlib
import logging
import pathlib
from typing import Dict, List

import click
import pydantic
from rich.console import Console
from rich.table import Table

import sunbeam.storage.backends
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

    def register_cli_commands(
        self, storage_group: click.Group, deployment: Deployment
    ) -> None:
        """Register all backend commands with the storage CLI group."""
        self._load_backends()

        # Register flat command structure
        self._register_add_commands(storage_group, deployment)
        self._register_remove_commands(storage_group, deployment)
        self._register_list_commands(storage_group, deployment)
        self._register_config_commands(storage_group, deployment)

    def _register_add_commands(
        self, storage_group: click.Group, deployment: Deployment
    ) -> None:
        """Register add commands: sunbeam storage add <backend> [key=value ...]."""

        @click.command()
        @click.argument("backend_type", type=click.Choice(list(self._backends.keys())))
        @click.argument(
            "config_args", nargs=-1
        )  # Accept variable number of key=value arguments
        @click.pass_context
        def add(ctx, backend_type: str, config_args: tuple):
            """Add a storage backend.

            Interactive mode (prompts for all required values):
              sunbeam storage add hitachi

            Inline configuration:
              sunbeam storage add hitachi name=my-hitachi serial=12345 \
                  pools=pool1,pool2 san_ip=192.168.1.100 san_password=secret
            """
            try:
                backend = self.get_backend(backend_type)
                config_class = backend.config_class

                # Parse configuration arguments
                config_dict = {}
                backend_name = None

                for arg in config_args:
                    if "=" not in arg:
                        raise click.BadParameter(
                            f"Configuration argument '{arg}' must be in "
                            "key=value format"
                        )
                    key, value = arg.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    config_dict[key] = value

                    # Extract backend name if provided
                    if key == "name":
                        backend_name = value

                # If no configuration provided, start interactive mode
                if not config_args:
                    console.print(
                        f"[blue]Setting up {backend.display_name} backend[/blue]"
                    )
                    # Prompt for backend name first
                    backend_name = click.prompt("Backend name", type=str)
                    config_instance = backend.prompt_for_config(backend_name)
                    # Ensure the config instance has the correct name
                    config_instance.name = backend_name
                else:
                    # Validate that name is provided
                    if not backend_name:
                        raise click.BadParameter(
                            "Backend name is required. Use: name=<backend-name>"
                        )

                    # Create configuration instance
                    try:
                        config_instance = config_class(**config_dict)
                    except pydantic.ValidationError as e:
                        console.print("[red]Configuration validation error:[/red]")
                        for error in e.errors():
                            field_name = error["loc"][0] if error["loc"] else "unknown"
                            console.print(f"  {field_name}: {error['msg']}")

                        # Show available fields for help
                        console.print(
                            "\n[yellow]Available configuration fields:[/yellow]"
                        )
                        fields = getattr(config_class, "model_fields", {})
                        for field_name, field in fields.items():
                            is_required = getattr(
                                field,
                                "is_required",
                                lambda: getattr(field, "required", False),
                            )()
                            required_text = " (required)" if is_required else ""
                            description = (
                                getattr(field, "description", None) or "No description"
                            )
                            console.print(
                                f"  {field_name}{required_text}: {description}"
                            )

                        raise click.Abort()

                # Add the backend
                backend.add_backend(deployment, backend_name, config_instance, console)
                # Success message is now handled by the backend method

            except Exception as e:
                console.print(f"[red]Error adding backend: {e}[/red]")
                raise click.Abort()

        storage_group.add_command(add)

    def _register_remove_commands(
        self, storage_group: click.Group, deployment: Deployment
    ) -> None:
        """Register remove commands: sunbeam storage remove <backend> <name>."""

        @click.command()
        @click.argument("backend_type", type=click.Choice(list(self._backends.keys())))
        @click.argument("backend_name", type=str)
        @click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
        @click.pass_context
        def remove(ctx, backend_type: str, backend_name: str, yes: bool):
            """Remove a Terraform-managed storage backend."""
            backend = self.get_backend(backend_type)

            # Check if backend exists in Terraform configuration
            if not backend.backend_exists(deployment, backend_name):
                console.print(f"[red]Error: Backend '{backend_name}' not found[/red]")
                raise click.Abort()

            if not yes:
                click.confirm(
                    f"Remove {backend.display_name} backend '{backend_name}'?",
                    abort=True,
                )

            try:
                backend.remove_backend(deployment, backend_name, console)
            except Exception as e:
                console.print(f"[red]Error removing backend: {e}[/red]")
                raise click.Abort()

        storage_group.add_command(remove)

    def _register_list_commands(
        self, storage_group: click.Group, deployment: Deployment
    ) -> None:
        """Register list commands: sunbeam storage list all."""

        @click.group()
        def list_cmd():
            """List storage backends."""
            pass

        @click.command()
        @click.pass_context
        def all(ctx):
            """List all storage backends."""
            service = StorageBackendService(deployment)
            backends = service.list_backends()

            if not backends:
                console.print("No storage backends found")
                return

            # Create a beautiful table for listing backends
            table = Table(
                title="Storage Backends",
                show_header=True,
                header_style="bold blue",
                border_style="blue",
                title_style="bold blue",
            )

            table.add_column("Backend Name", style="cyan", min_width=15)
            table.add_column("Type", style="magenta", min_width=8)
            table.add_column("Status", style="green", min_width=8)
            table.add_column("Charm", style="yellow", min_width=20)

            for backend in backends:
                table.add_row(
                    backend.name, backend.backend_type, backend.status, backend.charm
                )

            console.print(table)

        list_cmd.add_command(all)
        storage_group.add_command(list_cmd, name="list")

    def _register_config_commands(
        self, storage_group: click.Group, deployment: Deployment
    ) -> None:
        """Register config commands: sunbeam storage config <subcommands>."""

        @click.group()
        def config():
            """Manage storage backend configuration."""
            pass

        # Register individual config subcommands
        config.add_command(self._create_config_show_command(deployment))
        config.add_command(self._create_config_set_command())
        config.add_command(self._create_config_reset_command())
        config.add_command(self._create_config_options_command())
        storage_group.add_command(config)

    def _create_config_show_command(self, deployment: Deployment):
        """Create the config show command."""

        @click.command()
        @click.argument("backend_type", type=click.Choice(list(self._backends.keys())))
        @click.argument("backend_name", type=str)
        @click.pass_context
        def show(ctx, backend_type: str, backend_name: str):
            """Show current storage backend configuration in a formatted table."""
            service = StorageBackendService(deployment)
            config = service.get_backend_config(backend_name, backend_type)
            backend = self.get_backend(backend_type)

            self._display_config_table(backend, backend_name, config, backend_type)

        return show

    def _create_config_set_command(self):
        """Create the config set command."""

        @click.command()
        @click.argument("backend_type", type=click.Choice(list(self._backends.keys())))
        @click.argument("backend_name", type=str)
        @click.argument("config_pairs", nargs=-1, required=True)
        @click.pass_context
        def set_config(ctx, backend_type: str, backend_name: str, config_pairs: tuple):
            """Set storage backend configuration options."""
            config_updates = self._parse_config_pairs(config_pairs)
            self._execute_config_update(ctx, backend_type, backend_name, config_updates)

        return set_config

    def _create_config_reset_command(self):
        """Create the config reset command."""

        @click.command()
        @click.argument("backend_type", type=click.Choice(list(self._backends.keys())))
        @click.argument("backend_name", type=str)
        @click.argument("keys", nargs=-1, required=True)
        @click.pass_context
        def reset(ctx, backend_type: str, backend_name: str, keys: tuple):
            """Reset storage backend configuration options to defaults."""
            config_updates = {"_reset_keys": list(keys)}
            self._execute_config_reset(ctx, backend_type, backend_name, config_updates)

        return reset

    def _create_config_options_command(self):
        """Create the config options command."""

        @click.command()
        @click.argument("backend_type", type=click.Choice(list(self._backends.keys())))
        @click.argument("backend_name", type=str, required=False)
        @click.pass_context
        def options(ctx, backend_type: str, backend_name: str | None = None):
            """List available configuration options for backend."""
            backend = self.get_backend(backend_type)
            self._display_config_options(backend)

        return options

    def _display_config_table(
        self, backend, backend_name: str, config: dict, backend_type: str
    ):
        """Display configuration in a formatted table."""
        config_class = backend.config_class

        # Create a beautiful table
        table = Table(
            title=(
                f"Configuration for {backend.display_name} backend '{backend_name}'"
            ),
            show_header=True,
            header_style="bold blue",
            title_style="bold cyan",
            border_style="blue",
        )

        table.add_column("Option", style="cyan", no_wrap=True, width=30)
        table.add_column("Value", style="green", width=25)
        table.add_column("Description", style="dim", width=50)

        # Get field descriptions from the config class
        field_descriptions = self._get_field_descriptions(config_class)

        # Sort config items for better display
        sorted_config = sorted(config.items())

        for key, value in sorted_config:
            display_value = self._format_config_value(key, value)
            description = field_descriptions.get(key, "Configuration option")

            # Truncate long descriptions
            if len(description) > 47:
                description = description[:44] + "..."

            table.add_row(key, display_value, description)

        if not config:
            console.print(
                f"[yellow]No configuration found for {backend_type} "
                f"backend '{backend_name}'[/yellow]"
            )
        else:
            console.print(table)
            console.print(
                f"[green]Configuration displayed for "
                f"{backend.display_name} backend '{backend_name}'[/green]"
            )

    def _get_field_descriptions(self, config_class) -> dict:
        """Extract field descriptions from config class."""
        field_descriptions = {}
        if hasattr(config_class, "model_fields"):
            # Pydantic v2 style
            for field_name, field_info in config_class.model_fields.items():
                field_descriptions[field_name] = getattr(
                    field_info, "description", "No description available"
                )
        return field_descriptions

    def _format_config_value(self, key: str, value) -> str:
        """Format configuration value for display, masking sensitive data."""
        display_value = str(value)
        if any(
            sensitive in key.lower()
            for sensitive in ["password", "secret", "token", "key"]
        ):
            display_value = "*" * min(8, len(display_value)) if display_value else ""

        # Truncate long values for better display
        if len(display_value) > 23:
            display_value = display_value[:20] + "..."

        return display_value

    def _parse_config_pairs(self, config_pairs: tuple) -> dict:
        """Parse configuration key=value pairs."""
        config_updates = {}
        for pair in config_pairs:
            if "=" not in pair:
                raise click.BadParameter(
                    f"Invalid config pair: {pair}. Use key=value format."
                )
            key, value = pair.split("=", 1)
            config_updates[key] = value
        return config_updates

    def _execute_config_update(
        self, ctx, backend_type: str, backend_name: str, config_updates: dict
    ):
        """Execute configuration update operation."""
        deployment = ctx.obj
        backend = self.get_backend(backend_type)

        try:
            from sunbeam.core.common import run_plan
            from sunbeam.core.terraform import TerraformInitStep

            # Register terraform plan
            backend.register_terraform_plan(deployment)
            tfhelper = deployment.get_tfhelper(backend.tfplan)

            # Create update config step
            update_step = backend.create_update_config_step(
                deployment, backend_name, config_updates
            )

            plan = [TerraformInitStep(tfhelper), update_step]
            run_plan(plan, console)

            console.print(
                f"[green]Configuration updated for "
                f"{backend.display_name} backend '{backend_name}'[/green]"
            )

        except Exception as e:
            console.print(f"[red]❌ Failed to update configuration: {e}[/red]")
            raise click.ClickException(f"Configuration update failed: {e}")

    def _execute_config_reset(
        self, ctx, backend_type: str, backend_name: str, config_updates: dict
    ):
        """Execute configuration reset operation."""
        deployment = ctx.obj
        backend = self.get_backend(backend_type)

        try:
            from sunbeam.core.common import run_plan
            from sunbeam.core.terraform import TerraformInitStep

            # Register terraform plan
            backend.register_terraform_plan(deployment)
            tfhelper = deployment.get_tfhelper(backend.tfplan)

            # Create update config step with reset keys
            update_step = backend.create_update_config_step(
                deployment, backend_name, config_updates
            )

            plan = [TerraformInitStep(tfhelper), update_step]
            run_plan(plan, console)

            console.print(
                f"[green]Configuration reset for "
                f"{backend.display_name} backend '{backend_name}'[/green]"
            )

        except Exception as e:
            console.print(f"[red]❌ Failed to reset configuration: {e}[/red]")
            raise click.ClickException(f"Configuration reset failed: {e}")

    def _display_config_options(self, backend):
        """Display available configuration options for a backend."""
        console.print(
            f"[blue]Available configuration options for {backend.display_name}:[/blue]"
        )

        # Show basic configuration options from the backend's config class
        config_class = backend.config_class
        # Use model_fields for Pydantic v2
        fields = getattr(config_class, "model_fields", {})
        if fields:
            from rich.table import Table

            table = Table(show_header=True, header_style="bold blue")
            table.add_column("Option", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Default", style="yellow")
            table.add_column("Description", style="white")

            for field_name, field_info in fields.items():
                if field_name == "name":  # Skip the base name field
                    continue

                try:
                    field_type, default_value, description = self._extract_field_info(
                        field_info
                    )
                    table.add_row(field_name, field_type, default_value, description)
                except Exception:
                    # Fallback for any field access issues
                    table.add_row(field_name, "str", "Unknown", "Configuration option")

            console.print(table)
        else:
            console.print(
                "  Configuration options are managed dynamically via Terraform."
            )
            console.print(
                "  Use 'sunbeam storage config show' to see current configuration."
            )

    def _extract_field_info(self, field_info) -> tuple:
        """Extract field type, default value, and description from field info."""
        # Handle different pydantic versions
        if hasattr(field_info, "type_"):
            field_type = str(field_info.type_).replace("<class '", "").replace("'>", "")
        elif hasattr(field_info, "annotation"):
            field_type = (
                str(field_info.annotation).replace("<class '", "").replace("'>", "")
            )
        else:
            field_type = "str"  # fallback

        if hasattr(field_info, "default"):
            default_value = (
                str(field_info.default) if field_info.default is not ... else "Required"
            )
        else:
            default_value = "Unknown"

        if hasattr(field_info, "field_info") and hasattr(
            field_info.field_info, "description"
        ):
            description = field_info.field_info.description or "No description"
        elif hasattr(field_info, "description"):
            description = field_info.description or "No description"
        else:
            description = "No description"

        return field_type, default_value, description

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
