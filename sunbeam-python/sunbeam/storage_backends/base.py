# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any, Dict, List, Mapping

import click
from pydantic import BaseModel, Field
from rich.console import Console

from sunbeam.core.common import BaseStep, SunbeamException, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.features.interface.v1.base import BaseRegisterable

LOG = logging.getLogger(__name__)
console = Console()


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
        self.juju_helper = JujuHelper(deployment.juju_controller)
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
                if app_name.startswith("cinder-volume"):
                    backend_info = StorageBackendInfo(
                        name=app_name,
                        backend_type=self._get_backend_type(app_name),
                        status=app_info.get("status", {}).get("status", "unknown"),
                        charm=app_info.get("charm", "unknown"),
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

    @property
    def config_class(self) -> type[StorageBackendConfig]:
        """Return the configuration class for this backend."""
        return StorageBackendConfig

    def commands(
        self, conditions: Mapping[str, str | bool] = {}
    ) -> Dict[str, List[Dict]]:
        """Return commands for registration under storage group."""
        return {
            "storage.add": [{"name": self.name, "command": self._create_add_command()}],
            "storage.remove": [
                {"name": self.name, "command": self._create_remove_command()}
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
