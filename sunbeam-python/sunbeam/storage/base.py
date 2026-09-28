# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend base class with integrated Terraform functionality."""

import enum
import ipaddress
import json
import logging
import re
import types
import typing
from pathlib import Path
from typing import Any

import click
import pydantic
from packaging.version import Version
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    StorageBackendNotFoundException,
)
from sunbeam.core.common import BaseStep, run_plan
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest, StorageBackendConfig
from sunbeam.core.terraform import TerraformHelper, TerraformInitStep
from sunbeam.feature_gates import FeatureGateMixin
from sunbeam.steps.openstack import DeployControlPlaneStep
from sunbeam.storage.cli_base import StorageBackendCLIBase
from sunbeam.storage.models import (
    SecretDictField,
)
from sunbeam.storage.steps import (
    PRINCIPAL_HA_APPLICATION,
    PRINCIPAL_NON_HA_APPLICATION,
    SNAP_NAME_HA,
    SNAP_NAME_NON_HA,
    BaseStorageBackendDeployStep,
    BaseStorageBackendDestroyStep,
    DeploySpecificCinderVolumeStep,
    DestroySpecificCinderVolumeStep,
    ValidateStoragePrerequisitesStep,
    prompt_backend_config,
)

LOG = logging.getLogger(__name__)
console = Console()

# Juju application name validation pattern
# Based on Juju's naming rules: must start with letter, contain only
# letters, numbers, hyphens. Cannot end with hyphen, cannot have
# consecutive hyphens, cannot end with a purely numeric segment
JUJU_APP_NAME_PATTERN = re.compile(r"^(?:[a-z][a-z0-9]*(?:-[a-z0-9]*[a-z][a-z0-9]*)*)$")

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

    # Check that the segment after the final hyphen is not purely numeric
    # (Juju reserves the pattern <app-name>-<number> for unit names)
    if "-" in name:
        parts = name.split("-")
        last_part = parts[-1]
        if last_part.isdigit():
            return False

    return True


BackendConfig = typing.TypeVar("BackendConfig", bound=StorageBackendConfig)
ENABLED_BACKENDS_CONFIG_KEY = "StorageBackendsEnabled"


class StorageBackendBase(FeatureGateMixin, typing.Generic[BackendConfig]):
    """Base class for storage backends with integrated Terraform functionality.

    Inherits from FeatureGateMixin to provide feature gating capabilities.
    Storage backends can be gated by setting generally_available=False and requiring
    users to enable them via: snap set openstack storage.<backend-type>=true
    """

    backend_type: str = "base"
    display_name: str = "Base Storage Backend"
    version = Version("0.0.1")
    user_manifest = None  # Path to user manifest file
    # By default, storage backends are not generally available.
    generally_available: bool = False

    def __init__(self) -> None:
        """Initialize storage backend."""
        self.tfplan = "storage-backend-plan"
        self.tfplan_dir = "deploy-storage-backend"
        self._manifest: Manifest | None = None

    def check_enabled(self, client: Client | None, snap: Snap) -> bool:
        """Check if the backend is enabled in the deployment.

        This function checks if the backend is available based on:
        - generally_available flag
        - enabled backends in clusterd config
        - snap config for feature flag

        Args:
            client: Client instance
            snap: Snap instance
        Returns:
            True if enabled, False otherwise
        """
        # Check if feature gate allows this backend to be visible
        return not self.check_gated(
            client=client, snap=snap, enabled_config_key=ENABLED_BACKENDS_CONFIG_KEY
        )

    def enable_backend(self, client: Client) -> None:
        """Enable the backend in the deployment.

        Args:
            client: Client instance
        """
        try:
            enabled_backends = json.loads(
                client.cluster.get_config(ENABLED_BACKENDS_CONFIG_KEY)
            )
        except ConfigItemNotFoundException:
            enabled_backends = []

        if self.backend_type not in enabled_backends:
            enabled_backends.append(self.backend_type)
            client.cluster.update_config(
                ENABLED_BACKENDS_CONFIG_KEY, json.dumps(enabled_backends)
            )

    @property
    def _feature_key(self) -> str:
        """Return the feature key for this backend.

        Uses the FeatureGateMixin gate_key property for consistency.
        This property is kept for backwards compatibility.
        """
        return self.gate_key

    # Common CLI registration pattern (Abstraction 3: CLI registration)
    def register_add_cli(self, add: click.Group) -> None:  # noqa: F811
        """Register 'sunbeam storage add <backend>' command.

        Default implementation delegates to CLI class following the pattern.
        Subclasses can override if they need custom behavior.
        """
        cli_class = self._get_cli_class()
        cli = cli_class(self)
        cli.register_add_cli(add)

    def register_options_cli(self, options: click.Group) -> None:
        """Register 'sunbeam storage options <backend>' command.

        Default implementation delegates to CLI class following the pattern.
        Subclasses can override if they need custom behavior.
        """
        cli_class = self._get_cli_class()
        cli = cli_class(self)
        cli.register_options_cli(options)

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
        return "TerraformVarsStorageBackends"

    def config_key(self, name: str) -> str:
        """Config key for a specific backend instance."""
        return f"Storage-{name}"

    def create_deploy_step(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        preseed: dict,
        backend_name: str,
        model: str,
        accept_defaults: bool = False,
    ) -> BaseStep:
        """Create a deployment step for this backend."""
        return BaseStorageBackendDeployStep(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            preseed,
            backend_name,
            self,
            model,
            accept_defaults,
        )

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
        return BaseStorageBackendDestroyStep(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            backend_name,
            self,
            model,
        )

    def register_terraform_plan(self, deployment: Deployment) -> None:
        """Register storage backend Terraform plan with deployment system."""
        import shutil

        from sunbeam.core.terraform import TerraformHelper

        # Get the plan source path
        backend_self_contained = (
            Path(__file__).parent.parent.parent.parent.parent.parent
            / "etc/deploy-storage"  # / "backends" / self.name / self.tfplan_dir
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

    def add_backend_instance(
        self,
        deployment: Deployment,
        name: str,
        config: dict,
        console: Console,
        accept_defaults: bool = False,
    ) -> None:
        """Add a storage backend using Terraform deployment."""
        # Validate backend name follows Juju application naming rules
        if not validate_juju_application_name(name):
            raise click.ClickException(
                f"Invalid backend name '{name}'. "
                "Backend names must be valid Juju application names: "
                "start with a letter, contain only lowercase letters, numbers, "
                "and hyphens, cannot end with a hyphen, cannot "
                "have consecutive hyphens, and cannot end with a "
                "purely numeric segment after a hyphen."
            )

        openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
        # Register our Terraform plan with the deployment system
        self.register_terraform_plan(deployment)

        # Get standard Sunbeam helpers
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        # Validate prerequisites before collecting configuration, so
        # answers are only prompted for and saved when the deployment
        # can actually accept the backend
        run_plan(
            [ValidateStoragePrerequisitesStep(deployment, client, jhelper)], console
        )

        # Collect and validate configuration before building the plan so
        # decisions that depend on the configuration (e.g. HA support) are
        # made with the full configuration in hand
        variables = prompt_backend_config(
            client, self, self.manifest, name, config, console, accept_defaults
        )
        validated_config = self.config_type().model_validate(variables, by_name=True)
        supports_ha = self.supports_ha_for(validated_config)
        principal = (
            PRINCIPAL_HA_APPLICATION if supports_ha else PRINCIPAL_NON_HA_APPLICATION
        )

        # The principal application is chosen per configuration; changing
        # it on re-add would orphan the previously deployed application,
        # so the backend must be removed and added back instead
        try:
            existing_backend = client.cluster.get_storage_backend(name)
        except StorageBackendNotFoundException:
            existing_backend = None
        if existing_backend is not None and existing_backend.principal != principal:
            raise click.ClickException(
                f"Changing the configuration of backend {name!r} requires a "
                f"different cinder-volume application "
                f"({existing_backend.principal} -> {principal}). "
                f"Remove the backend and add it again to apply the change: "
                f"'sunbeam storage remove {name}'"
            )

        plan = [
            TerraformInitStep(tfhelper),
            TerraformInitStep(openstack_tfhelper),
            DeploySpecificCinderVolumeStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                self.manifest,
                name,
                self,
                deployment.openstack_machines_model,
                supports_ha=supports_ha,
            ),
            self.create_deploy_step(
                deployment,
                client,
                tfhelper,
                jhelper,
                self.manifest,
                variables,
                name,
                deployment.openstack_machines_model,
                accept_defaults,
            ),
            DeployControlPlaneStep(
                deployment,
                openstack_tfhelper,
                jhelper,
                self.manifest,
                "auto",
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console)

    def _get_field_descriptions(self, config_class: type[BackendConfig]) -> dict:
        """Extract field descriptions from a Pydantic v2 model class."""
        desc: dict[str, str] = {}
        for field_name, field_info in config_class.model_fields.items():
            desc[field_name] = field_info.description or "No description available"
        return desc

    def _field_is_secret(self, finfo) -> bool:
        """Check if a field is marked as a secret."""
        for constraint in finfo.metadata:
            if isinstance(constraint, SecretDictField):
                return True
        return False

    def _format_config_value(self, value, is_secret: bool) -> str:
        """Format configuration value for display, masking sensitive data."""
        display_value = str(value)
        if is_secret:
            display_value = "*" * min(8, len(display_value)) if display_value else ""
        if len(display_value) > 23:
            display_value = display_value[:20] + "..."
        return display_value

    def _format_type(self, annotation: type) -> str:
        """Return a consistent, human-readable representation of a type annotation."""
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)

        # Handle Optional / Union[..., None]
        if origin is typing.Union and type(None) in args:
            non_none_args = [a for a in args if a is not type(None)]
            inner = " | ".join(self._format_type(a) for a in non_none_args)
            return inner

        # Handle other Unions
        if origin in (typing.Union, types.UnionType):
            if args and args[-1] is type(None):
                args = args[:-1]
            return " | ".join(self._format_type(a) for a in args)

        # Handle Literal types
        if origin is typing.Literal:
            values = ", ".join(repr(a) for a in args)
            return f"{values}"

        # Handle Enum subclasses
        if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
            # Display allowed values (stringified)
            options = ", ".join(repr(e.value) for e in annotation)
            return f"{options}"

        # Handle parametrised generics, e.g., list[str], dict[str, int]
        if origin is not None:
            origin_name = getattr(origin, "__name__", str(origin))
            inner = ", ".join(self._format_type(a) for a in args)
            return f"{origin_name}[{inner}]" if args else origin_name

        # Handle bare types like int, bool, str, MyClass
        if hasattr(annotation, "__name__"):
            return annotation.__name__

        # Handle special typing constructs (e.g. Any)
        return str(annotation)

    def _extract_field_info(self, field_info: pydantic.fields.FieldInfo) -> tuple:
        """Extract field type, default value, and description from field info."""
        if field_info.annotation:
            field_type = self._format_type(field_info.annotation)
        else:
            field_type = "str"

        if field_info.is_required():
            field_type += " [red]Required[/red]"

        description = field_info.description or "No description"

        return field_type, description

    def display_config_options(self) -> None:
        """Display available configuration options for this backend."""
        console.print(
            f"[blue]Available configuration options for {self.display_name}:[/blue]"
        )
        fields = self.config_type().model_fields
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
        table.add_column("Description", style="white")

        for field_name, finfo in fields.items():
            if field_name == "name":
                continue
            try:
                ftype, descr = self._extract_field_info(finfo)
                table.add_row(field_name, ftype, descr)
            except Exception:
                table.add_row(field_name, "str", "Configuration option")

        console.print(table)

    def display_config_table(self, backend_name: str, config: BackendConfig) -> None:
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

        field_descriptions = self._get_field_descriptions(self.config_type())
        for field, finfo in self.config_type().model_fields.items():
            value = getattr(config, field, None)
            if not value:
                continue
            # Skip empty values (None, empty string, empty dict, empty list)
            # But keep 0 and False as valid values
            if (
                value is None
                or value == ""
                or (isinstance(value, (dict, list)) and len(value) == 0)
            ):
                continue

            display_value = self._format_config_value(
                value, is_secret=self._field_is_secret(finfo)
            )
            description = field_descriptions.get(field, "Configuration option")
            if len(description) > 47:
                description = description[:44] + "..."
            table.add_row(utils.to_kebab(field), display_value, description)

        if not config:
            console.print(
                (
                    f"[yellow]No configuration found for {self.backend_type} "
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
        openstack_tfhelper = deployment.get_tfhelper("openstack-plan")
        # Register our Terraform plan with the deployment system
        self.register_terraform_plan(deployment)

        # Get standard Sunbeam helpers
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        # Principal recorded when the backend was added; the deploy step
        # deletes the clusterd record, so read it before the plan runs
        principal = None
        try:
            principal = client.cluster.get_storage_backend(backend_name).principal
        except StorageBackendNotFoundException:
            LOG.debug("Backend %s not found in clusterd", backend_name)
        # Create removal plan - each backend should implement its own destroy step
        plan = [
            ValidateStoragePrerequisitesStep(deployment, client, jhelper),
            TerraformInitStep(tfhelper),
            TerraformInitStep(openstack_tfhelper),
            self.create_destroy_step(
                deployment,
                client,
                tfhelper,
                jhelper,
                self.manifest,
                backend_name,
                deployment.openstack_machines_model,
            ),
            DestroySpecificCinderVolumeStep(
                deployment,
                client,
                tfhelper,
                jhelper,
                self.manifest,
                backend_name,
                self,
                deployment.openstack_machines_model,
                principal_application=principal,
            ),
            DeployControlPlaneStep(
                deployment,
                openstack_tfhelper,
                jhelper,
                self.manifest,
                "auto",
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console)

    def config_type(self) -> type[BackendConfig]:
        """Return the configuration class for this backend."""
        raise NotImplementedError("Subclasses must implement config_type")

    # Backend-specific properties that subclasses should override
    @property
    def charm_name(self) -> str:
        """Charm name for this backend."""
        raise NotImplementedError("Subclasses must define charm_name")

    @property
    def charm_channel(self) -> str:
        """Charm channel for this backend."""
        return "latest/stable"

    @property
    def charm_revision(self) -> str | None:
        """Charm revision for this backend."""
        return None

    @property
    def charm_base(self) -> str:
        """Charm base for this backend."""
        return "ubuntu@22.04"

    @property
    def principal_application(self) -> str:
        """Principal application for this backend."""
        return (
            PRINCIPAL_HA_APPLICATION
            if self.supports_ha
            else PRINCIPAL_NON_HA_APPLICATION
        )

    @property
    def snap_name(self) -> str:
        """Snap name for this backend.

        Returns a name with an underscore for parallel snap installation.
        note(gboutry): Backend can redefine which snap to install for principal
        application.
        """
        return SNAP_NAME_HA if self.supports_ha else SNAP_NAME_NON_HA

    @property
    def supports_ha(self) -> bool:
        """Return whether this backend supports HA deployments."""
        return False

    def supports_ha_for(self, config: BackendConfig) -> bool:
        """Return whether a specific backend configuration supports HA.

        Backends whose HA support depends on the configuration (e.g. the
        selected protocol) override this. The static supports_ha property
        remains the fallback when no configuration is available.
        """
        return self.supports_ha

    def get_endpoint_bindings(self, deployment: Deployment) -> list[dict[str, str]]:
        """Endpoint bindings for this backend."""
        return [
            {"space": deployment.get_space(Networks.MANAGEMENT)},
            {
                "endpoint": "cinder-volume",
                "space": deployment.get_space(Networks.STORAGE),
            },
        ]

    def build_terraform_vars(
        self,
        deployment: Deployment,
        manifest: Manifest,
        backend_name: str,
        config: BackendConfig,
    ) -> dict[str, Any]:
        """Generate Terraform variables for Pure Storage backend deployment."""
        # Map our configuration fields to the correct charm configuration option names
        config_dict = config.model_dump(exclude_none=True, by_alias=True)

        # Secret fields that will be translated to juju secrets
        # K: config field name, V: field key in juju secret
        secret_fields = {}
        alias_generator = self.config_type().model_config.get("alias_generator")
        if alias_generator is None:
            raise RuntimeError(
                "Alias generator not defined in config model StorageBackendConfig"
            )
        # raise if alias generator is callable
        if not hasattr(alias_generator, "generate_aliases"):
            raise RuntimeError(
                "Alias generator is not of type AliasGenerator in"
                " config model StorageBackendConfig"
            )
        for fname, finfo in self.config_type().model_fields.items():
            for constraint in finfo.metadata:
                if isinstance(constraint, SecretDictField):
                    secret_fields[alias_generator.generate_aliases(fname)[2]] = (  # type: ignore
                        constraint.field
                    )

        charm_channel = self.charm_channel
        charm_revision = None
        if backends_cfg := manifest.storage.root.get(self.backend_type):
            if backend_cfg := backends_cfg.root.get(backend_name):
                if charm_cfg := backend_cfg.software.charms.get(self.charm_name):
                    if channel := charm_cfg.channel:
                        charm_channel = channel
                    if revision := charm_cfg.revision:
                        charm_revision = revision

        supports_ha = self.supports_ha_for(config)

        # Build Terraform variables to match the plan's expected format
        tfvars = {
            "principal_application": (
                PRINCIPAL_HA_APPLICATION
                if supports_ha
                else PRINCIPAL_NON_HA_APPLICATION
            ),
            "charm_name": self.charm_name,
            "charm_base": self.charm_base,
            "charm_channel": charm_channel,
            "charm_revision": charm_revision,
            "endpoint_bindings": self.get_endpoint_bindings(deployment),
            "charm_config": config_dict,
            "secrets": secret_fields,
        }

        return tfvars

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

    def _get_cli_class(self) -> type[StorageBackendCLIBase]:
        """Get the CLI class for this backend.

        Subclasses should override this to return their CLI class.
        Default implementation attempts to import based on naming convention.
        """
        try:
            # Try to import CLI class based on naming convention
            module_path = f"sunbeam.storage.backends.{self.backend_type}.cli"
            cli_module = __import__(
                module_path, fromlist=[f"{self.backend_type.title()}CLI"]
            )
            cli_class_name = f"{self.backend_type.title()}CLI"
            return getattr(cli_module, cli_class_name)
        except (ImportError, AttributeError):
            LOG.debug(
                "%s does not implement custom cli class",
                self.backend_type,
            )
            return StorageBackendCLIBase
