# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend base class with integrated Terraform functionality."""

import ipaddress
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import click
from packaging.version import Version
from rich.console import Console
from rich.table import Table

from sunbeam.core.common import BaseStep, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformHelper, TerraformInitStep

from .models import (
    BackendAlreadyExistsException,
    BackendNotFoundException,
    StorageBackendConfig,
)
from .service import StorageBackendService
from .steps import ValidateStoragePrerequisitesStep

LOG = logging.getLogger(__name__)
console = Console()

# Juju application name validation pattern
# Based on Juju's naming rules: must start with letter, contain only
# letters, numbers, hyphens. Cannot end with hyphen, cannot have
# consecutive hyphens, cannot have numbers after final hyphen
JUJU_APP_NAME_PATTERN = re.compile(r"^[a-z]([a-z0-9]*(-[a-z0-9]*)*)?$")

# Regex pattern for validating FQDN (Fully Qualified Domain Name)
FQDN_PATTERN = (
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)


def validate_juju_application_name(name: str) -> bool:
    """Validate that a name is a valid Juju application name.

    Args:
        name: The application name to validate

    Returns:
        True if valid, False otherwise
    """
    if not name:
        return False

    # Check basic pattern
    if not JUJU_APP_NAME_PATTERN.match(name):
        return False

    # Additional checks for edge cases
    if name.endswith("-"):
        return False

    if "--" in name:
        return False

    # Check that numbers don't appear after the final hyphen
    if "-" in name:
        parts = name.split("-")
        last_part = parts[-1]
        if any(char.isdigit() for char in last_part):
            return False

    return True


class StorageBackendBase(ABC):
    """Base class for storage backends with integrated Terraform functionality."""

    name: str = "base"
    display_name: str = "Base Storage Backend"
    version = Version("0.0.1")
    tf_plan_location = "FEATURE_REPO"  # Plans stored in feature directory
    user_manifest = None  # Path to user manifest file

    def __init__(self):
        """Initialize storage backend."""
        self.tfplan = "storage-backend-plan"
        self.tfplan_dir = "deploy-storage-backend"
        self._manifest: Optional[Manifest] = None
        self.service: Optional[StorageBackendService] = None

    def _get_service(self, deployment: Deployment) -> StorageBackendService:
        """Get or create the storage backend service."""
        if self.service is None:
            self.service = StorageBackendService(deployment)
        return self.service

    # Common CLI registration pattern (Abstraction 3: CLI registration)
    def register_add_cli(self, add: click.Group) -> None:  # noqa: F811
        """Register 'sunbeam storage add <backend>' command.

        Default implementation delegates to CLI class following the pattern.
        Subclasses can override if they need custom behavior.
        """
        cli_class = self._get_cli_class()
        cli = cli_class(self)
        cli.register_add_cli(add)

    def register_cli(  # noqa: F811
        self,
        remove: click.Group,
        config_show: click.Group,
        config_set: click.Group,
        config_options: click.Group,
        deployment: Deployment,
    ) -> None:
        """Register management commands for this backend.

        Default implementation delegates to CLI class following the pattern.
        Subclasses can override if they need custom behavior.
        """
        cli_class = self._get_cli_class()
        cli = cli_class(self)
        cli.register_cli(remove, config_show, config_set, config_options, deployment)

    # Terraform-related properties and methods
    @property
    def manifest(self) -> Manifest:
        """Return the manifest."""
        if self._manifest:
            return self._manifest

        manifest = click.get_current_context().obj.get_manifest(self.user_manifest)
        self._manifest = manifest
        if self._manifest is None:
            raise ValueError("Failed to load manifest")
        return self._manifest

    @property
    def tfvar_config_key(self) -> str:
        """Config key for storing Terraform variables in clusterd."""
        return f"TerraformVarsStorageBackends{self.name.title()}"

    # Abstract methods that each backend must implement
    @abstractmethod
    def create_deploy_step(
        self,
        deployment: Deployment,
        client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        backend_name: str,
        backend_config: StorageBackendConfig,
        model: str,
    ) -> BaseStep:
        """Create a deployment step for this backend."""
        pass

    @abstractmethod
    def create_destroy_step(
        self,
        deployment: Deployment,
        client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        backend_name: str,
        model: str,
    ) -> BaseStep:
        """Create a destruction step for this backend."""
        pass

    @abstractmethod
    def create_update_config_step(
        self,
        deployment: Deployment,
        backend_name: str,
        config_updates: Dict[str, Any],
    ) -> BaseStep:
        """Create a configuration update step for this backend."""
        pass

    def register_terraform_plan(self, deployment: Deployment) -> None:
        """Register storage backend Terraform plan with deployment system."""
        import shutil

        from sunbeam.core.terraform import TerraformHelper

        # Get the plan source path
        backend_self_contained = (
            Path(__file__).parent / "backends" / self.name / self.tfplan_dir
        )

        if backend_self_contained.exists():
            plan_source = backend_self_contained
        else:
            raise FileNotFoundError(
                f"Terraform plan not found at {backend_self_contained}"
            )

        # Copy plan to deployment's plans directory
        dst = deployment.plans_directory / self.tfplan_dir
        shutil.copytree(plan_source, dst, dirs_exist_ok=True)

        # Create TerraformHelper
        env = {}
        env.update(deployment._get_juju_clusterd_env())
        env.update(deployment.get_proxy_settings())

        tfhelper = TerraformHelper(
            path=dst,
            plan=self.tfplan,
            tfvar_map={},
            backend="http",
            env=env,
            clusterd_address=deployment.get_clusterd_http_address(),
        )

        # Register the helper with the deployment's tfhelpers
        deployment._tfhelpers[self.tfplan] = tfhelper

    def add_backend(
        self,
        deployment: Deployment,
        backend_name: str,
        config: StorageBackendConfig,
        console: Console,
    ) -> None:
        """Add a storage backend using Terraform deployment."""
        # Validate backend name follows Juju application naming rules
        if not validate_juju_application_name(backend_name):
            raise click.ClickException(
                f"Invalid backend name '{backend_name}'. "
                f"Backend names must be valid Juju application names: "
                f"start with a letter, contain only lowercase letters, numbers,"
                f"and hyphens, cannot end with hyphen, cannot"
                f"have consecutive hyphens, and cannot have numbers"
                f"after the final hyphen."
            )

        service = self._get_service(deployment)
        if service.backend_exists(backend_name, self.name):
            raise BackendAlreadyExistsException(
                f"Backend '{backend_name}' already exists"
            )

        # Register our Terraform plan with the deployment system
        self.register_terraform_plan(deployment)

        # Get standard Sunbeam helpers
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        plan = [
            ValidateStoragePrerequisitesStep(deployment, client, jhelper),
            TerraformInitStep(tfhelper),
            self.create_deploy_step(
                deployment,
                client,
                tfhelper,
                jhelper,
                self.manifest,
                backend_name,
                config,
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console)

    def _get_field_descriptions(self, config_class) -> dict:
        """Extract field descriptions from a Pydantic v2 model class."""
        desc: Dict[str, str] = {}
        if hasattr(config_class, "model_fields"):
            for field_name, field_info in config_class.model_fields.items():
                desc[field_name] = getattr(
                    getattr(field_info, "field_info", field_info),
                    "description",
                    "No description available",
                )
        return desc

    def _format_config_value(self, key: str, value) -> str:
        """Format configuration value for display, masking sensitive data."""
        display_value = str(value)
        if any(s in key.lower() for s in ["password", "secret", "token", "key"]):
            display_value = "*" * min(8, len(display_value)) if display_value else ""
        if len(display_value) > 23:
            display_value = display_value[:20] + "..."
        return display_value

    def _extract_field_info(self, field_info) -> tuple:
        """Extract field type, default value, and description from field info."""
        if hasattr(field_info, "type_"):
            field_type = str(field_info.type_).replace("<class '", "").replace("'>", "")
        elif hasattr(field_info, "annotation"):
            field_type = (
                str(field_info.annotation).replace("<class '", "").replace("'>", "")
            )
        else:
            field_type = "str"

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

    def display_config_options(self) -> None:
        """Display available configuration options for this backend."""
        console.print(
            f"[blue]Available configuration options for {self.display_name}:[/blue]"
        )
        config_class = self.config_class
        fields = getattr(config_class, "model_fields", {})
        if not fields:
            console.print(
                "  Configuration options are managed dynamically via Terraform."
            )
            console.print(
                "  Use 'sunbeam storage config show' to see current configuration."
            )
            return

        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Option", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Default", style="yellow")
        table.add_column("Description", style="white")

        for field_name, finfo in fields.items():
            if field_name == "name":
                continue
            try:
                ftype, default, descr = self._extract_field_info(finfo)
                table.add_row(field_name, ftype, default, descr)
            except Exception:
                table.add_row(field_name, "str", "Unknown", "Configuration option")

        console.print(table)

    def display_config_table(self, backend_name: str, config: dict) -> None:
        """Display current configuration in a formatted table for this backend."""
        table = Table(
            title=f"Configuration for {self.display_name} backend '{backend_name}'",
            show_header=True,
            header_style="bold blue",
            title_style="bold cyan",
            border_style="blue",
        )

        table.add_column("Option", style="cyan", no_wrap=True, width=30)
        table.add_column("Value", style="green", width=25)
        table.add_column("Description", style="dim", width=50)

        field_descriptions = self._get_field_descriptions(self.config_class)
        for key, value in sorted(config.items()):
            # Skip empty values (None, empty string, empty dict, empty list)
            # But keep 0 and False as valid values
            if (
                value is None
                or value == ""
                or (isinstance(value, (dict, list)) and len(value) == 0)
            ):
                continue

            display_value = self._format_config_value(key, value)
            description = field_descriptions.get(key, "Configuration option")
            if len(description) > 47:
                description = description[:44] + "..."
            table.add_row(key, display_value, description)

        if not config:
            console.print(
                (
                    f"[yellow]No configuration found for {self.name} "
                    f"backend '{backend_name}'[/yellow]"
                )
            )
        else:
            console.print(table)
            console.print(
                (
                    f"[green]Configuration displayed for {self.display_name} "
                    f"backend '{backend_name}'[/green]"
                )
            )

    def remove_backend(
        self, deployment: Deployment, backend_name: str, console: Console
    ) -> None:
        """Remove a storage backend using Terraform."""
        service = self._get_service(deployment)
        if not service.backend_exists(backend_name, self.name):
            raise BackendNotFoundException(f"Backend '{backend_name}' not found")

        # Register our Terraform plan with the deployment system
        self.register_terraform_plan(deployment)

        # Get standard Sunbeam helpers
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        # Create removal plan - each backend should implement its own destroy step
        plan = [
            ValidateStoragePrerequisitesStep(deployment, client, jhelper),
            TerraformInitStep(tfhelper),
            self.create_destroy_step(
                deployment,
                client,
                tfhelper,
                jhelper,
                self.manifest,
                backend_name,
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console)

    def update_backend_config(
        self, deployment: Deployment, backend_name: str, config_updates: Dict[str, Any]
    ) -> None:
        """Update backend configuration using Terraform."""
        service = self._get_service(deployment)
        if not service.backend_exists(backend_name, self.name):
            raise BackendNotFoundException(f"Backend '{backend_name}' not found")

        # Ensure the Terraform plan is registered so we can obtain its tfhelper
        self.register_terraform_plan(deployment)

        plan = [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
            self.create_update_config_step(deployment, backend_name, config_updates),
        ]

        run_plan(plan, console)

    @property
    def config_class(self) -> type[StorageBackendConfig]:
        """Return the configuration class for this backend."""
        return StorageBackendConfig

    # Backend-specific properties that subclasses should override
    @property
    def backend_type(self) -> str:
        """Backend type identifier. Override in subclasses."""
        return self.name

    @property
    def charm_name(self) -> str:
        """Charm name for this backend. Override in subclasses."""
        raise NotImplementedError("Subclasses must define charm_name")

    @property
    def charm_channel(self) -> str:
        """Charm channel for this backend. Override in subclasses."""
        return "stable"

    @property
    def charm_revision(self) -> Optional[int]:
        """Charm revision for this backend. Override in subclasses."""
        return None

    @property
    def charm_base(self) -> str:
        """Charm base for this backend. Override in subclasses."""
        return "ubuntu@22.04"

    @property
    def backend_endpoint(self) -> str:
        """Backend endpoint name for integration. Override in subclasses."""
        return "cinder-volume"

    @property
    def units(self) -> int:
        """Number of units to deploy. Override in subclasses."""
        return 1

    @property
    def additional_integrations(self) -> List[str]:
        """Additional integrations for this backend. Override in subclasses."""
        return []

    @abstractmethod
    def get_terraform_variables(
        self, backend_name: str, config: StorageBackendConfig, model: str
    ) -> Dict[str, Any]:
        """Generate Terraform variables for this backend. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement get_terraform_variables")

    def get_field_mapping(self) -> Dict[str, str]:
        """Get mapping from config fields to charm config options.

        Maps Pydantic field names (with underscores) to charm config option
        names (with hyphens). Uses the config_class to automatically generate
        the mapping from Pydantic model fields.
        """
        config_class = self.config_class
        # Use model_fields for Pydantic v2
        model_fields = getattr(config_class, "model_fields", {})
        field_names = model_fields.keys() if model_fields else []

        return {key: key.replace("_", "-") for key in field_names}

    @abstractmethod
    def prompt_for_config(self, backend_name: str) -> StorageBackendConfig:
        """Prompt user for backend-specific configuration. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement prompt_for_config")

    # Common utility methods
    def _filter_config_for_charm(
        self,
        config_dict: Dict[str, Any],
        field_mapping: Dict[str, str],
        exclude_fields: Set[str] | None = None,
    ) -> Dict[str, Any]:
        """Filter configuration dictionary for charm deployment.

        Only includes explicitly set values (non-default, non-empty) to avoid
        sending unnecessary configuration to the charm.

        Args:
            config_dict: Configuration dictionary to filter
            field_mapping: Mapping from config keys to charm config keys
            exclude_fields: Set of fields to exclude from filtering

        Returns:
            Filtered charm configuration dictionary
        """
        exclude_fields = exclude_fields or set()

        # Get default values for comparison
        default_config = self._get_default_config()
        default_dict = default_config.model_dump()

        charm_config = {}
        for key, value in config_dict.items():
            # Skip excluded fields
            if key in exclude_fields:
                continue

            if key in field_mapping:
                # Only include explicitly set values (non-default, non-empty)
                if self._should_include_config_value(key, value, default_dict.get(key)):
                    charm_config[field_mapping[key]] = value

        return charm_config

    def _should_include_config_value(
        self, key: str, value: Any, default_value: Any
    ) -> bool:
        """Determine if a configuration value should be included in charm config.

        Args:
            key: Configuration field name
            value: Current value
            default_value: Default value for this field

        Returns:
            True if the value should be sent to the charm, False otherwise
        """
        # Always include the 'name' field as it's required
        if key == "name":
            return True

        # Skip None values
        if value is None:
            return False

        # Skip empty strings
        if isinstance(value, str) and value.strip() == "":
            return False

        # Skip empty lists
        if isinstance(value, list) and len(value) == 0:
            return False

        # Skip empty dictionaries
        if isinstance(value, dict) and len(value) == 0:
            return False

        # Skip values that match the default
        if value == default_value:
            return False

        # Include all other values
        return True

    @abstractmethod
    def _get_default_config(self) -> StorageBackendConfig:
        """Get a default configuration instance for comparison.

        Subclasses must implement this to provide a default config instance
        with dummy values for required fields.
        """
        raise NotImplementedError("Subclasses must implement _get_default_config")

    def _get_credential_fields(self) -> Set[str]:
        """Get set of credential field names that should be excluded from charm config.

        Subclasses can override this to specify which fields contain credentials
        that should not be sent to the charm configuration.

        Returns:
            Set of field names containing credentials
        """
        return {"name"}  # Default: only exclude the name field

    # Common utility methods (Abstraction 2: IP/FQDN validation)
    @staticmethod
    def _validate_ip_or_fqdn(value: str) -> str:
        """Validate IP address or FQDN.

        Args:
            value: IP address or FQDN to validate

        Returns:
            The validated value

        Raises:
            click.BadParameter: If value is not a valid IP or FQDN
        """
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError:
            # If not a valid IP, check if it's a valid FQDN
            if re.match(FQDN_PATTERN, value):
                return value
            raise click.BadParameter("Must be a valid IP address or FQDN")

    def _get_cli_class(self):
        """Get the CLI class for this backend.

        Subclasses should override this to return their CLI class.
        Default implementation attempts to import based on naming convention.
        """
        try:
            # Try to import CLI class based on naming convention
            module_path = f"sunbeam.storage.backends.{self.name}.cli"
            cli_module = __import__(module_path, fromlist=[f"{self.name.title()}CLI"])
            cli_class_name = f"{self.name.title()}CLI"
            return getattr(cli_module, cli_class_name)
        except (ImportError, AttributeError):
            raise NotImplementedError(
                f"Subclasses must implement _get_cli_class or "
                f"follow naming convention: "
                f"sunbeam.storage.backends.{self.name}.cli.{self.name.title()}CLI"
            )
