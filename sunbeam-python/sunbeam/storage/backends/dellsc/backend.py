# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Dell Storage Center storage backend implementation using base step classes."""

import logging
from typing import Any, Dict, Literal, Set

import click

try:
    import yaml as _yaml  # type: ignore

    yaml: Any = _yaml
except Exception:  # yaml optional; handle gracefully at runtime
    yaml = None

# Import pydantic Field directly
from pydantic import Field
from rich.console import Console

from sunbeam.core.common import BaseStep
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformHelper
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import StorageBackendConfig
from sunbeam.storage.steps import (
    BaseStorageBackendConfigUpdateStep,
    BaseStorageBackendDeployStep,
    BaseStorageBackendDestroyStep,
)

LOG = logging.getLogger(__name__)
console = Console()


class DellSCConfig(StorageBackendConfig):
    """Static configuration model for Dell Storage Center storage backend.

    This model includes all configuration options supported by the
    cinder-volume-dellsc charm as defined in charmcraft.yaml.
    """

    # Required fields (inherited from StorageBackendConfig)
    # name: str (from base class)

    # Mandatory connection parameters
    san_ip: str = Field(..., description="Dell Storage Center management IP or hostname")
    san_username: str = Field(..., description="SAN management username")
    san_password: str = Field(..., description="SAN management password")
    dell_sc_ssn: int = Field(default=64702, description="Storage Center System Serial Number")
    protocol: Literal["fc", "iscsi"] = Field(
        default="fc", description="Front-end protocol (fc or iscsi)"
    )

    # Backend configuration
    volume_backend_name: str = Field(
        default="", description="Name that Cinder will report for this backend"
    )
    backend_availability_zone: str = Field(
        default="", description="Availability zone to associate with this backend"
    )

    # Dell Storage Center specific options
    dell_sc_api_port: int = Field(
        default=3033, description="Dell Storage Center API port"
    )
    dell_sc_server_folder: str = Field(
        default="openstack", description="Server folder name on Dell SC"
    )
    dell_sc_volume_folder: str = Field(
        default="openstack", description="Volume folder name on Dell SC"
    )
    dell_server_os: str = Field(
        default="Red Hat Linux 6.x", description="Server OS type for Dell SC"
    )
    dell_sc_verify_cert: bool = Field(
        default=False, description="Verify SSL certificate for Dell SC API"
    )

    # Provisioning options
    san_thin_provision: bool = Field(
        default=True, description="Enable thin provisioning"
    )

    # Domain and network filtering
    excluded_domain_ips: str = Field(
        default="", description="Comma-separated list of excluded domain IPs"
    )
    included_domain_ips: str = Field(
        default="", description="Comma-separated list of included domain IPs"
    )

    # Dual DSM configuration
    secondary_san_ip: str = Field(
        default="", description="Secondary Dell Storage Center management IP"
    )
    secondary_san_username: str = Field(
        default="Admin", description="Secondary SAN management username"
    )
    secondary_san_password: str = Field(
        default="", description="Secondary SAN management password"
    )
    secondary_sc_api_port: int = Field(
        default=3033, description="Secondary Dell Storage Center API port"
    )

    # API timeout configuration
    dell_api_async_rest_timeout: int = Field(
        default=15, description="Async REST API timeout in seconds"
    )
    dell_api_sync_rest_timeout: int = Field(
        default=30, description="Sync REST API timeout in seconds"
    )

    # SSH connection settings
    ssh_conn_timeout: int = Field(
        default=30, description="SSH connection timeout in seconds"
    )
    ssh_max_pool_conn: int = Field(
        default=5, description="Maximum SSH pool connections"
    )
    ssh_min_pool_conn: int = Field(
        default=1, description="Minimum SSH pool connections"
    )

    # Juju secrets for credentials (not charm config options)
    san_credentials_secret: str = Field(
        default="", description="Juju secret URI for SAN credentials"
    )
    secondary_san_credentials_secret: str = Field(
        default="", description="Juju secret URI for secondary SAN credentials"
    )


class DellSCBackend(StorageBackendBase):
    """Dell Storage Center storage backend implementation."""

    name = "dellsc"
    display_name = "Dell Storage Center"
    charm_name = "cinder-volume-dellsc"

    def __init__(self):
        """Initialize Dell Storage Center backend."""
        super().__init__()
        self.tfplan = "dellsc-backend-plan"
        self.tfplan_dir = "deploy-dellsc-backend"

    charm_channel = (
        "latest/edge"  # Use edge for development, change to stable for production
    )
    charm_revision = 3
    charm_base = "ubuntu@24.04"
    backend_endpoint = "cinder-volume"
    units = 1
    additional_integrations = []

    @property
    def config_class(self) -> type[StorageBackendConfig]:
        """Return the configuration class for Dell Storage Center backend."""
        return DellSCConfig

    def _get_credential_fields(self) -> Set[str]:
        """Get set of credential field names that should be excluded from charm config.

        For Dell SC backend, we exclude all credential fields and secret URIs.
        """
        return {
            # meta
            "name",
            # primary array credentials
            "san_username",
            "san_password",
            # secondary array credentials
            "secondary_san_username",
            "secondary_san_password",
            # juju secret URIs
            "san_credentials_secret",
            "secondary_san_credentials_secret",
        }

    def get_field_mapping(self) -> Dict[str, str]:
        """Get mapping from config fields to charm config options.

        Uses base class automatic mapping and excludes credential fields.
        """
        # Start from the base automatic mapping
        mapping = super().get_field_mapping()

        # Exclude credential fields
        exclude = self._get_credential_fields()
        return {k: v for k, v in mapping.items() if k not in exclude}

    def get_terraform_variables(
        self, backend_name: str, config: StorageBackendConfig, model: str
    ) -> Dict[str, Any]:
        """Generate Terraform variables for Dell Storage Center backend deployment."""
        # Map our configuration fields to the correct charm configuration option names
        config_dict = config.model_dump()
        field_mapping = self.get_field_mapping()

        # Filter config using base class method, excluding credential fields
        charm_config = self._filter_config_for_charm(
            config_dict, field_mapping, exclude_fields=self._get_credential_fields()
        )

        # Build Terraform variables to match the plan's expected format
        tfvars = {
            "machine_model": model,
            "charm_dellsc_name": self.charm_name,
            "charm_dellsc_base": self.charm_base,
            "charm_dellsc_channel": self.charm_channel,
            "charm_dellsc_endpoint": self.backend_endpoint,
            "charm_dellsc_revision": self.charm_revision,
            "dellsc_backends": {
                backend_name: {
                    "charm_config": charm_config,
                    # Main array credentials (always required)
                    "san_username": config_dict.get("san_username", ""),
                    "san_password": config_dict.get("san_password", ""),
                    # Secondary array credentials (optional)
                    "secondary_san_username": config_dict.get("secondary_san_username", ""),
                    "secondary_san_password": config_dict.get("secondary_san_password", ""),
                }
            },
        }

        return tfvars

    def _get_default_config(self) -> DellSCConfig:
        """Get a default configuration instance for comparison.
        
        This creates a config instance with all fields set to their Pydantic defaults,
        allowing proper filtering of default values in _filter_config_for_charm().
        """
        # Create instance with minimal required fields, letting Pydantic set defaults
        return DellSCConfig(
            name="dummy",  # Required field
            san_ip="dummy",  # Required field  
            san_username="dummy",  # Required field
            san_password="dummy",  # Required field, noqa: S106
            # All other fields will use their Pydantic Field() defaults:
            # dell_sc_ssn=64702, protocol="fc", volume_backend_name="", 
            # backend_availability_zone="", dell_sc_api_port=3033, etc.
        )

    def prompt_for_config(self, backend_name: str) -> DellSCConfig:
        """Prompt user for Dell Storage Center-specific configuration."""
        return self._prompt_for_config(backend_name)

    def _prompt_for_config(self, backend_name: str) -> DellSCConfig:
        """Prompt user for Dell Storage Center backend configuration."""
        console.print(
            "\n[bold blue]Dell Storage Center Backend Configuration[/bold blue]"
        )
        console.print("Please provide the required configuration options:")

        # Prompt for required fields
        san_ip = click.prompt(
            "Management IP/FQDN", type=str, value_proc=self._validate_ip_or_fqdn
        )
        dell_sc_ssn = click.prompt(
            "Storage Center System Serial Number", type=int, default=64702
        )
        protocol = click.prompt(
            "Protocol",
            type=click.Choice(["fc", "iscsi"], case_sensitive=False),
            default="fc",
        )

        # Main array credentials (will be automatically converted to Juju secret)
        console.print("\n[bold yellow]Array Credentials[/bold yellow]")
        console.print(
            "These credentials will be automatically stored in a Juju secret."
        )
        san_username = click.prompt("SAN username", type=str, default="admin")
        san_password = click.prompt("SAN password", type=str, hide_input=True)

        # Optional: prompt for volume backend name (defaults to backend name)
        volume_backend_name = click.prompt(
            "Volume backend name", type=str, default=backend_name, show_default=True
        )

        # Optional: Dual DSM configuration
        secondary_san_username = ""
        secondary_san_password = ""
        secondary_san_ip = ""

        configure_dual_dsm = click.confirm(
            "\nConfigure dual DSM (high availability)?", default=False
        )
        if configure_dual_dsm:
            console.print("\n[bold yellow]Secondary DSM Configuration[/bold yellow]")
            secondary_san_ip = click.prompt(
                "Secondary management IP/FQDN", type=str, value_proc=self._validate_ip_or_fqdn
            )
            secondary_san_username = click.prompt(
                "Secondary SAN username", type=str, default="Admin"
            )
            secondary_san_password = click.prompt(
                "Secondary SAN password", type=str, hide_input=True
            )

        return DellSCConfig(
            name=backend_name,
            san_ip=san_ip,
            san_username=san_username,
            san_password=san_password,
            dell_sc_ssn=dell_sc_ssn,
            protocol=protocol,
            volume_backend_name=volume_backend_name,
            secondary_san_ip=secondary_san_ip,
            secondary_san_username=secondary_san_username,
            secondary_san_password=secondary_san_password,
        )

    # Implementation of abstract methods from StorageBackendBase
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
        """Create a deployment step for Dell Storage Center backend."""
        return DellSCDeployStep(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            backend_name,
            backend_config,
            self,
            model,
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
        """Create a destruction step for Dell Storage Center backend."""
        return DellSCDestroyStep(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            backend_name,
            self,
            model,
        )

    def create_update_config_step(
        self,
        deployment: Deployment,
        backend_name: str,
        config_updates: Dict[str, Any],
    ) -> BaseStep:
        """Create a configuration update step for Dell Storage Center backend."""
        return DellSCUpdateConfigStep(
            deployment,
            self,
            backend_name,
            config_updates,
        )


# Dell Storage Center-specific step implementations using base step classes
class DellSCDeployStep(BaseStorageBackendDeployStep):
    """Deploy Dell Storage Center storage backend using base step class."""

    def get_terraform_variables(self) -> Dict[str, Any]:
        """Get Terraform variables for Dell Storage Center backend deployment."""
        return self.backend_instance.get_terraform_variables(
            self.backend_name, self.backend_config, self.model
        )


class DellSCDestroyStep(BaseStorageBackendDestroyStep):
    """Destroy Dell Storage Center storage backend using base step class."""


class DellSCUpdateConfigStep(BaseStorageBackendConfigUpdateStep):
    """Update Dell Storage Center storage backend configuration using base step class."""
