# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any, Dict, List, Mapping

import click
import jubilant
from pydantic import BaseModel, Field
from rich.console import Console

from sunbeam.core.common import BaseStep, SunbeamException, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper, JujuException, ApplicationNotFoundException
from sunbeam.features.interface.v1.base import BaseRegisterable

LOG = logging.getLogger(__name__)
console = Console()


class ExtendedJujuHelper(JujuHelper):
    """Extended JujuHelper with additional configuration management methods."""

    def set_app_config(self, app: str, config: Dict[str, Any], model: str) -> None:
        """Set configuration for an application.

        :app: Name of the application.
        :config: Configuration dictionary with key-value pairs to set.
        :model: Name of the model.
        """
        with self._model(model) as juju:
            try:
                juju.config(app, config)
            except jubilant.CLIError as e:
                if "not found" in e.stderr:
                    raise ApplicationNotFoundException(f"App {app!r} not found") from e
                raise JujuException(
                    f"Failed to set config for application {app!r}: {e.stderr}"
                ) from e

    def reset_app_config(self, app: str, config_keys: List[str], model: str) -> None:
        """Reset configuration keys to their default values for an application.

        :app: Name of the application.
        :config_keys: List of configuration keys to reset.
        :model: Name of the model.
        """
        with self._model(model) as juju:
            try:
                # Use juju config --reset to reset specific keys
                for key in config_keys:
                    juju.config(app, **{key: None}, reset=[key])
            except jubilant.CLIError as e:
                if "not found" in e.stderr:
                    raise ApplicationNotFoundException(f"App {app!r} not found") from e
                raise JujuException(
                    f"Failed to reset config for application {app!r}: {e.stderr}"
                ) from e


class StorageBackendException(SunbeamException):
    """Base exception for storage backend operations."""

    pass


class BackendNotFoundException(StorageBackendException):
    """Raised when storage backend is not found."""

    pass


class BackendAlreadyExistsException(StorageBackendException):
    """Raised when storage backend already exists."""

    pass


class BackendValidationException(StorageBackendException):
    """Raised when storage backend configuration is invalid."""

    pass


class StorageBackendConfig(BaseModel):
    """Base configuration model for storage backends."""

    name: str = Field(..., description="Backend name")

    class Config:
        """Pydantic configuration for StorageBackendConfig."""

        extra = "allow"  # Allow backend-specific fields


class StorageBackendInfo(BaseModel):
    """Information about a deployed storage backend."""

    name: str
    backend_type: str
    status: str
    charm: str
    config: Dict[str, Any] = {}


class StorageBackendService:
    """Service layer for storage backend operations."""

    def __init__(self, deployment: Deployment):
        self.deployment = deployment
        self.juju_helper = ExtendedJujuHelper(deployment.juju_controller)
        self.model = self._get_model_name()

    def _get_model_name(self) -> str:
        """Get the OpenStack machines model name."""
        model = self.deployment.openstack_machines_model
        if not model.startswith("admin/"):
            model = f"admin/{model}"
        return model

    def add_backend(self, backend_type: str, config: StorageBackendConfig) -> None:
        """Add a storage backend with proper error handling."""
        try:
            # Check if backend already exists
            if self.backend_exists(config.name):
                raise BackendAlreadyExistsException(
                    f"Backend '{config.name}' already exists"
                )

            LOG.info(f"Adding {backend_type} backend '{config.name}'")
            console.print(
                f"[blue]Adding {backend_type} backend '{config.name}'...[/blue]"
            )

            # Backend-specific deployment logic will be handled by subclasses

        except Exception as e:
            LOG.error(f"Failed to add backend '{config.name}': {e}")
            raise StorageBackendException(f"Failed to add backend: {e}") from e

    def remove_backend(self, backend_name: str) -> None:
        """Remove a storage backend safely."""
        try:
            if not self.backend_exists(backend_name):
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            LOG.info(f"Removing backend '{backend_name}'")
            console.print(f"[yellow]Removing backend '{backend_name}'...[/yellow]")

            # Remove the application
            self.juju_helper.remove_application(backend_name, model=self.model)

            # Wait for removal to complete
            self.juju_helper.wait_application_gone([backend_name], model=self.model)

            console.print(
                f"[green]Backend '{backend_name}' removed successfully[/green]"
            )

        except Exception as e:
            LOG.error(f"Failed to remove backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to remove backend: {e}") from e

    def list_backends(self) -> List[StorageBackendInfo]:
        """List all deployed storage backends."""
        try:
            status = self.juju_helper.get_model_status(self.model)
            # Handle both dict and Status object types
            if hasattr(status, "get"):
                apps = status.get("applications", {})
            elif isinstance(status, dict):
                apps = status.get("applications", {})
            else:
                apps = {}

            backends = []
            for app_name, app_info in apps.items():
                # Check if this is a storage backend by charm name
                charm_name = app_info.get("charm", "")
                if self._is_storage_backend(charm_name, app_name):
                    backend_info = StorageBackendInfo(
                        name=app_name,
                        backend_type=self._get_backend_type_from_charm(charm_name, app_name),
                        status=app_info.get("status", {}).get("status", "unknown"),
                        charm=charm_name,
                        config=app_info.get("charm-config", {}),
                    )
                    backends.append(backend_info)

            return backends

        except Exception as e:
            LOG.error(f"Failed to list backends: {e}")
            raise StorageBackendException(f"Failed to list backends: {e}") from e

    def backend_exists(self, backend_name: str) -> bool:
        """Check if a backend exists."""
        try:
            apps = self.juju_helper.get_application_names(self.model)
            return backend_name in apps
        except Exception:
            return False

    def _get_backend_type(self, app_name: str) -> str:
        """Determine backend type from application name."""
        if "hitachi" in app_name:
            return "hitachi"
        elif "ceph" in app_name:
            return "ceph"
        else:
            return "unknown"
    
    def _is_storage_backend(self, charm_name: str, app_name: str) -> bool:
        """Check if an application is a storage backend."""
        # Known storage backend charms
        storage_charms = [
            "cinder-volume",
            "cinder-volume-hitachi", 
            "cinder-volume-ceph",
            "cinder-volume-netapp",
            "cinder-volume-pure"
        ]
        
        # Check by charm name
        for storage_charm in storage_charms:
            if charm_name == storage_charm or charm_name.startswith(storage_charm):
                return True
        
        # Also check by application name patterns for legacy compatibility
        if app_name.startswith("cinder-volume"):
            return True
            
        return False
    
    def _get_backend_type_from_charm(self, charm_name: str, app_name: str) -> str:
        """Determine backend type from charm name and application name."""
        if "hitachi" in charm_name or "hitachi" in app_name:
            return "hitachi"
        elif "ceph" in charm_name or "ceph" in app_name:
            return "ceph"
        elif "netapp" in charm_name or "netapp" in app_name:
            return "netapp"
        elif "pure" in charm_name or "pure" in app_name:
            return "pure"
        elif charm_name == "cinder-volume":
            return "cinder-volume"
        else:
            return "unknown"

    def get_backend_config(self, backend_name: str) -> Dict[str, Any]:
        """Get the current configuration of a storage backend."""
        try:
            if not self.backend_exists(backend_name):
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            LOG.info(f"Getting configuration for backend '{backend_name}'")
            config = self.juju_helper.get_app_config(backend_name, model=self.model)
            return config

        except Exception as e:
            LOG.error(f"Failed to get config for backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to get backend config: {e}") from e

    def set_backend_config(self, backend_name: str, config_updates: Dict[str, Any]) -> None:
        """Set configuration options for a storage backend."""
        try:
            if not self.backend_exists(backend_name):
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            LOG.info(f"Setting configuration for backend '{backend_name}'")
            console.print(f"[blue]Updating configuration for backend '{backend_name}'...[/blue]")

            # Apply configuration updates
            self.juju_helper.set_app_config(backend_name, config_updates, model=self.model)

            console.print(f"[green]Configuration updated successfully for '{backend_name}'[/green]")

        except Exception as e:
            LOG.error(f"Failed to set config for backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to set backend config: {e}") from e

    def reset_backend_config(self, backend_name: str, config_keys: List[str]) -> None:
        """Reset configuration options to their default values for a storage backend."""
        try:
            if not self.backend_exists(backend_name):
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            LOG.info(f"Resetting configuration for backend '{backend_name}'")
            console.print(f"[blue]Resetting configuration for backend '{backend_name}'...[/blue]")

            # Reset configuration keys to default
            self.juju_helper.reset_app_config(backend_name, config_keys, model=self.model)

            console.print(f"[green]Configuration reset successfully for '{backend_name}'[/green]")

        except Exception as e:
            LOG.error(f"Failed to reset config for backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to reset backend config: {e}") from e

    def get_charm_config_schema(self, charm_name: str) -> Dict[str, Any]:
        """Get the configuration schema for a charm dynamically."""
        try:
            LOG.info(f"Getting configuration schema for charm '{charm_name}'")
            
            # Use juju show-charm to get charm metadata including config options
            try:
                result = self.juju_helper.cli("show-charm", charm_name)
                charm_info = result
                
                # Extract config options from charm metadata
                config_options = {}
                if isinstance(charm_info, dict) and "config" in charm_info:
                    config_section = charm_info["config"]
                    if "options" in config_section:
                        config_options = config_section["options"]
                
                return config_options
                
            except Exception as e:
                LOG.warning(f"Failed to get charm schema via show-charm: {e}")
                # Fallback: try to get schema from deployed application
                return self._get_config_schema_from_app(charm_name)
                    
        except Exception as e:
            LOG.error(f"Failed to get charm config schema for '{charm_name}': {e}")
            raise StorageBackendException(f"Failed to get charm config schema: {e}") from e

    def _get_config_schema_from_app(self, charm_name: str) -> Dict[str, Any]:
        """Fallback method to get config schema from a deployed application."""
        try:
            # Find an application using this charm
            apps = self.list_backends()
            target_app = None
            
            for backend in apps:
                if charm_name in backend.charm:
                    target_app = backend.name
                    break
            
            if not target_app:
                LOG.warning(f"No deployed application found for charm '{charm_name}'")
                return {}
            
            # Get the current config which includes schema information
            config = self.juju_helper.get_app_config(target_app, model=self.model)
            
            # Extract schema information from config (Juju includes type and description info)
            schema = {}
            for key, value in config.items():
                if isinstance(value, dict) and "description" in value:
                    schema[key] = {
                        "type": value.get("type", "string"),
                        "description": value.get("description", ""),
                        "default": value.get("default"),
                    }
            
            return schema
            
        except Exception as e:
            LOG.warning(f"Failed to get config schema from deployed app: {e}")
            return {}

    def get_backend_config_options(self, backend_name: str) -> Dict[str, Any]:
        """Get available configuration options for a backend dynamically."""
        try:
            if not self.backend_exists(backend_name):
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            # Get the charm name for this backend
            backends = self.list_backends()
            charm_name = None
            for backend in backends:
                if backend.name == backend_name:
                    charm_name = backend.charm
                    break
            
            if not charm_name:
                raise BackendNotFoundException(f"Could not determine charm for backend '{backend_name}'")
            
            # Get the configuration schema for this charm
            config_schema = self.get_charm_config_schema(charm_name)
            
            return config_schema

        except Exception as e:
            LOG.error(f"Failed to get config options for backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to get backend config options: {e}") from e


class StorageBackendBase(BaseRegisterable):
    """Base class for storage backends following sunbeam patterns."""

    name: str = "base"
    display_name: str = "Base Storage Backend"

    def __init__(self):
        super().__init__()
        self.service: StorageBackendService = None

    def _get_service(self, deployment: Deployment) -> StorageBackendService:
        """Get or create the storage backend service."""
        if self.service is None:
            self.service = StorageBackendService(deployment)
        return self.service

    def _get_backend_type(self, app_name: str) -> str:
        """Determine backend type from application name."""
        if "hitachi" in app_name:
            return "hitachi"
        elif "ceph" in app_name:
            return "ceph"
        else:
            return "unknown"

    @property
    def config_class(self) -> type[StorageBackendConfig]:
        """Return the configuration class for this backend."""
        return StorageBackendConfig

    def commands(
        self, conditions: Mapping[str, str | bool] = {}
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return commands for registration under storage group."""
        return {
            "storage.add": [{"name": self.name, "command": self._create_add_command()}],
            "storage.remove": [
                {"name": self.name, "command": self._create_remove_command()}
            ],
            "storage.config": [
                {"name": self.name, "command": self._create_config_command()}
            ],
            "storage.set-config": [
                {"name": self.name, "command": self._create_set_config_command()}
            ],
            "storage.reset-config": [
                {"name": self.name, "command": self._create_reset_config_command()}
            ],
            "storage.config-options": [
                {"name": self.name, "command": self._create_config_options_command()}
            ],
        }

    def _prompt_for_config(self) -> Dict[str, Any]:
        """Prompt user for backend configuration. Override in subclasses."""
        return {}

    def _create_add_plan(
        self, deployment: Deployment, config: Any, local_charm: str = ""
    ) -> List[BaseStep]:
        """Create a plan for adding a storage backend. Override in subclasses."""
        return []

    def _create_remove_plan(
        self, deployment: Deployment, backend_name: str
    ) -> List[BaseStep]:
        """Create a plan for removing a storage backend. Override in subclasses."""
        return []

    def _create_add_command(self) -> click.Command:
        """Create the add command for this backend."""

        @click.command(name=self.name)
        @click.option(
            "--local-charm",
            type=click.Path(exists=True, dir_okay=True, file_okay=True),
            help="""Path to local charm directory or .charm file for
            development (overrides charm store)""",
        )
        @click.pass_context
        def add_backend(ctx, local_charm):
            """Add a storage backend."""
            deployment = ctx.obj
            if not isinstance(deployment, Deployment):
                raise click.ClickException("Invalid deployment context")

            console = Console()
            try:
                # Get configuration from user
                config_data = self._prompt_for_config()
                config = self.config_class(**config_data)

                # Create and run deployment plan with interactive steps
                plan = self._create_add_plan(deployment, config, local_charm)
                run_plan(plan, console)

                console.print(
                    f"[green]✓ {self.name} backend '{config.name}' "
                    f"added successfully[/green]"
                )

            except Exception as e:
                console.print(f"[red]✗ Failed to add {self.name} backend: {e}[/red]")
                raise click.ClickException(str(e))

        return add_backend

    def _create_remove_command(self) -> click.Command:
        """Create the remove command for this backend."""

        @click.command(name=self.name)
        @click.argument("backend_name")
        @click.pass_context
        def remove_backend(ctx, backend_name: str):
            """Remove a storage backend."""
            deployment = ctx.obj
            if not isinstance(deployment, Deployment):
                raise click.ClickException("Invalid deployment context")

            console = Console()
            try:
                # Create and run removal plan with interactive steps
                plan = self._create_remove_plan(deployment, backend_name)
                run_plan(plan, console)

                console.print(
                    f"[green]✓ {self.name} backend '{backend_name}' "
                    f"removed successfully[/green]"
                )

            except Exception as e:
                console.print(
                    f"[red]✗ Failed to remove {self.name} backend "
                    f"'{backend_name}': {e}[/red]"
                )
                raise click.ClickException(str(e))

        return remove_backend

    def _create_config_command(self) -> click.Command:
        """Create the config command for this backend."""
        @click.command(name=self.name)
        @click.argument("backend_name")
        @click.pass_context
        def config_backend(ctx, backend_name: str):
            """View backend configuration."""
            deployment = ctx.obj
            service = self._get_service(deployment)
            console = Console()
            
            try:
                config = service.get_backend_config(backend_name)
                console.print(f"\n[bold]Configuration for {self.name} backend '{backend_name}':[/bold]")
                
                from rich.table import Table
                table = Table(show_header=True, header_style="bold magenta")
                table.add_column("Key", style="cyan")
                table.add_column("Value", style="green")
                table.add_column("Type", style="yellow")
                
                for key, value in config.items():
                    # Mask sensitive values
                    display_value = "***" if any(sensitive in key.lower() for sensitive in ["password", "secret", "key"]) else str(value)
                    table.add_row(key, display_value, type(value).__name__)
                
                console.print(table)
                
            except Exception as e:
                console.print(f"[red]✗ Failed to get {self.name} backend config: {e}[/red]")
                raise click.ClickException(str(e))
        
        return config_backend

    def _create_set_config_command(self) -> click.Command:
        """Create the set-config command for this backend."""
        @click.command(name=self.name)
        @click.argument("backend_name")
        @click.argument("config_pairs", nargs=-1, required=True)
        @click.pass_context
        def set_config_backend(ctx, backend_name: str, config_pairs: tuple):
            """Set backend configuration options.
            
            Usage: sunbeam storage set-config {backend} {backend_name} key1=value1 [key2=value2 ...]
            """
            deployment = ctx.obj
            service = self._get_service(deployment)
            console = Console()
            
            try:
                # Parse key=value pairs
                config_updates = {}
                for pair in config_pairs:
                    if '=' not in pair:
                        raise click.ClickException(f"Invalid format '{pair}'. Use key=value format.")
                    key, value = pair.split('=', 1)  # Split only on first '=' to handle values with '='
                    config_updates[key] = value
                
                if not config_updates:
                    raise click.ClickException("No configuration pairs provided. Use key=value format.")
                
                service.set_backend_config(backend_name, config_updates)
                
                # Display what was set
                pairs_str = ', '.join([f"{k}={v}" for k, v in config_updates.items()])
                console.print(f"[green]✓ Set {pairs_str} for {self.name} backend '{backend_name}'[/green]")
                
            except Exception as e:
                console.print(f"[red]✗ Failed to set {self.name} backend config: {e}[/red]")
                raise click.ClickException(str(e))
        
        return set_config_backend

    def _create_reset_config_command(self) -> click.Command:
        """Create the reset-config command for this backend."""
        @click.command(name=self.name)
        @click.argument("backend_name")
        @click.argument("keys", nargs=-1, required=True)
        @click.pass_context
        def reset_config_backend(ctx, backend_name: str, keys: tuple):
            """Reset backend configuration options to defaults."""
            deployment = ctx.obj
            service = self._get_service(deployment)
            console = Console()
            
            try:
                service.reset_backend_config(backend_name, list(keys))
                console.print(f"[green]✓ Reset {', '.join(keys)} for {self.name} backend '{backend_name}'[/green]")
                
            except Exception as e:
                console.print(f"[red]✗ Failed to reset {self.name} backend config: {e}[/red]")
                raise click.ClickException(str(e))
        
        return reset_config_backend

    def _create_config_options_command(self) -> click.Command:
        """Create the config-options command for this backend."""
        @click.command(name=self.name)
        @click.argument("backend_name", required=False)
        @click.pass_context
        def config_options_backend(ctx, backend_name: str = None):
            """List available configuration options for backend."""
            deployment = ctx.obj
            service = self._get_service(deployment)
            console = Console()
            
            try:
                options = service.get_backend_config_options(backend_name or f"cinder-volume-{self.name}")
                console.print(f"\n[bold]Available configuration options for {self.name} backend:[/bold]")
                
                from rich.table import Table
                table = Table(show_header=True, header_style="bold magenta")
                table.add_column("Option", style="cyan")
                table.add_column("Type", style="yellow")
                table.add_column("Default", style="green")
                table.add_column("Description", style="white")
                
                for option, details in options.items():
                    table.add_row(
                        option,
                        details.get("type", "string"),
                        str(details.get("default", "")),
                        details.get("description", "")
                    )
                
                console.print(table)
                
            except Exception as e:
                console.print(f"[red]✗ Failed to get {self.name} backend config options: {e}[/red]")
                raise click.ClickException(str(e))
        
        return config_options_backend
