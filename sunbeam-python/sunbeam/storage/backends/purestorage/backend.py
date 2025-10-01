# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Pure Storage FlashArray backend implementation using base step classes."""

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


class PureStorageConfig(StorageBackendConfig):
    """Configuration model for Pure Storage FlashArray backend.

    This model includes the essential configuration options for deploying
    a Pure Storage backend. Additional configuration can be managed dynamically
    through the charm configuration system.
    """

    # Required fields (inherited from StorageBackendConfig)
    # name: str (from base class)

    # Mandatory connection parameters
    san_ip: str = Field(
        ..., description="Pure Storage FlashArray management IP or hostname"
    )
    pure_api_token: str = Field(
        ..., description="REST API authorization token from FlashArray"
    )
    protocol: Literal["iscsi", "fc", "nvme"] = Field(
        default="fc", description="Pure Storage protocol (iscsi, fc, nvme)"
    )

    # Optional backend configuration
    volume_backend_name: str = Field(
        default="", description="Name that Cinder will report for this backend"
    )
    backend_availability_zone: str = Field(
        default="", description="Availability zone to associate with this backend"
    )

    # Protocol-specific options
    pure_iscsi_cidr: str = Field(
        default="0.0.0.0/0",
        description="CIDR of FlashArray iSCSI targets hosts can connect to",
    )
    pure_iscsi_cidr_list: str = Field(
        default="", description="Comma-separated list of CIDR for iSCSI targets"
    )
    pure_nvme_cidr: str = Field(
        default="0.0.0.0/0",
        description="CIDR of FlashArray NVMe targets hosts can connect to",
    )
    pure_nvme_cidr_list: str = Field(
        default="", description="Comma-separated list of CIDR for NVMe targets"
    )
    pure_nvme_transport: Literal["roce", "tcp"] = Field(
        default="roce", description="NVMe transport layer (roce or tcp)"
    )

    # Host and protocol tuning
    pure_host_personality: str = Field(
        default="", description="Host personality for protocol tuning"
    )

    # Storage management
    pure_automatic_max_oversubscription_ratio: bool = Field(
        default=True, description="Automatically determine oversubscription ratio"
    )
    pure_eradicate_on_delete: bool = Field(
        default=False,
        description="Immediately eradicate volumes on delete "
        "(WARNING: not recoverable)",
    )

    # Replication settings
    pure_replica_interval_default: int = Field(
        default=3600, description="Snapshot replication interval in seconds"
    )
    pure_replica_retention_short_term_default: int = Field(
        default=14400,
        description="Retain all snapshots on target for this time (seconds)",
    )
    pure_replica_retention_long_term_per_day_default: int = Field(
        default=3, description="Retain how many snapshots for each day"
    )
    pure_replica_retention_long_term_default: int = Field(
        default=7, description="Retain snapshots per day on target for this time (days)"
    )
    pure_replication_pg_name: str = Field(
        default="cinder-group",
        description="Pure Protection Group name for async replication",
    )
    pure_replication_pod_name: str = Field(
        default="cinder-pod", description="Pure Pod name for sync replication"
    )

    # Advanced replication
    pure_trisync_enabled: bool = Field(
        default=False, description="Enable 3-site replication (sync + async)"
    )
    pure_trisync_pg_name: str = Field(
        default="cinder-trisync",
        description="Protection Group name for trisync replication",
    )

    # SSL and security
    driver_ssl_cert_verify: bool = Field(
        default=False, description="Enable SSL certificate verification"
    )
    driver_ssl_cert_path: str = Field(
        default="", description="Path to SSL certificate file or directory"
    )

    # Performance options
    use_multipath_for_image_xfer: bool = Field(
        default=True, description="Enable multipathing for image transfer operations"
    )


class PureStorageBackend(StorageBackendBase):
    """Pure Storage FlashArray backend implementation."""

    name = "purestorage"
    display_name = "Pure Storage FlashArray"
    charm_name = "cinder-volume-purestorage"

    def __init__(self):
        """Initialize Pure Storage backend."""
        super().__init__()
        self.tfplan = "purestorage-backend-plan"
        self.tfplan_dir = "deploy-purestorage-backend"

    charm_channel = (
        "latest/edge"  # Use edge for development, change to stable for production
    )
    charm_revision = None  # Let Juju pick the latest
    charm_base = "ubuntu@24.04"
    backend_endpoint = "cinder-volume"
    units = 1
    additional_integrations = []

    @property
    def config_class(self) -> type[StorageBackendConfig]:
        """Return the configuration class for Pure Storage backend."""
        return PureStorageConfig

    def _get_credential_fields(self) -> Set[str]:
        """Get set of credential field names that should be excluded from charm config.

        For Pure Storage backend, we only exclude the meta name field.
        """
        return {"name"}  # Pure Storage uses API token directly in config

    def get_field_mapping(self) -> Dict[str, str]:
        """Get mapping from config fields to charm config options.

        Uses base class automatic mapping and excludes credential fields.
        """
        # Start from the base automatic mapping
        mapping = super().get_field_mapping()

        # Exclude credential fields
        exclude = self._get_credential_fields()
        return {k: v for k, v in mapping.items() if k not in exclude}

    # CLI registration uses base class implementation

    def get_terraform_variables(
        self, backend_name: str, config: StorageBackendConfig, model: str
    ) -> Dict[str, Any]:
        """Generate Terraform variables for Pure Storage backend deployment."""
        # Map our configuration fields to the correct charm configuration option names
        config_dict = config.model_dump()
        field_mapping = self.get_field_mapping()

        # Filter config using base class method
        charm_config = self._filter_config_for_charm(
            config_dict, field_mapping, exclude_fields=self._get_credential_fields()
        )

        # Build Terraform variables to match the plan's expected format
        tfvars = {
            "machine_model": model,
            "charm_purestorage_name": self.charm_name,
            "charm_purestorage_base": self.charm_base,
            "charm_purestorage_channel": self.charm_channel,
            "charm_purestorage_endpoint": self.backend_endpoint,
            "charm_purestorage_revision": self.charm_revision,
            "purestorage_backends": {
                backend_name: {
                    "charm_config": charm_config,
                }
            },
        }

        return tfvars

    def _get_default_config(self) -> PureStorageConfig:
        """Get a default configuration instance for comparison."""
        return PureStorageConfig(
            name="dummy",
            san_ip="dummy",
            pure_api_token="dummy",  # noqa: S106
        )

    def prompt_for_config(self, backend_name: str) -> PureStorageConfig:
        """Prompt user for Pure Storage-specific configuration."""
        return self._prompt_for_config(backend_name)

    # IP/FQDN validation uses base class implementation

    def _prompt_for_config(self, backend_name: str) -> PureStorageConfig:
        """Prompt user for Pure Storage backend configuration."""
        console.print(
            "\n[bold blue]Pure Storage FlashArray Backend Configuration[/bold blue]"
        )
        console.print("Please provide the required configuration options:")

        # Prompt for required fields
        san_ip = click.prompt(
            "FlashArray management IP/FQDN",
            type=str,
            value_proc=self._validate_ip_or_fqdn,
        )
        pure_api_token = click.prompt("Pure API token", type=str, hide_input=True)
        protocol = click.prompt(
            "Protocol",
            type=click.Choice(["iscsi", "fc", "nvme"], case_sensitive=False),
            default="fc",
        )

        # Optional: prompt for volume backend name (defaults to backend name)
        volume_backend_name = click.prompt(
            "Volume backend name", type=str, default=backend_name, show_default=True
        )

        # Protocol-specific configuration
        pure_iscsi_cidr = "0.0.0.0/0"
        pure_nvme_cidr = "0.0.0.0/0"
        pure_nvme_transport: Literal["roce", "tcp"] = "roce"

        if protocol.lower() == "iscsi":
            console.print("\n[bold yellow]iSCSI Configuration[/bold yellow]")
            pure_iscsi_cidr = click.prompt(
                "iSCSI target CIDR", type=str, default="0.0.0.0/0", show_default=True
            )
        elif protocol.lower() == "nvme":
            console.print("\n[bold yellow]NVMe Configuration[/bold yellow]")
            pure_nvme_cidr = click.prompt(
                "NVMe target CIDR", type=str, default="0.0.0.0/0", show_default=True
            )
            transport_choice = click.prompt(
                "NVMe transport",
                type=click.Choice(["roce", "tcp"], case_sensitive=False),
                default="roce",
            )
            pure_nvme_transport = transport_choice  # type: ignore[assignment]

        # Optional: Host personality
        pure_host_personality = ""
        if click.confirm(
            "\nConfigure host personality for protocol tuning?", default=False
        ):
            personalities = [
                "aix",
                "esxi",
                "hitachi-vsp",
                "hpux",
                "oracle-vm-server",
                "solaris",
                "vms",
            ]
            pure_host_personality = click.prompt(
                "Host personality",
                type=click.Choice(personalities, case_sensitive=False),
                default="esxi",
            )

        # Optional: Storage management settings
        pure_eradicate_on_delete = False
        if click.confirm(
            "\nEnable immediate volume eradication on delete?"
            "(WARNING: not recoverable)",
            default=False,
        ):
            pure_eradicate_on_delete = True

        return PureStorageConfig(
            name=backend_name,
            san_ip=san_ip,
            pure_api_token=pure_api_token,
            protocol=protocol,
            volume_backend_name=volume_backend_name,
            pure_iscsi_cidr=pure_iscsi_cidr,
            pure_nvme_cidr=pure_nvme_cidr,
            pure_nvme_transport=pure_nvme_transport,
            pure_host_personality=pure_host_personality,
            pure_eradicate_on_delete=pure_eradicate_on_delete,
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
        """Create a deployment step for Pure Storage backend."""
        return PureStorageDeployStep(
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
        """Create a destruction step for Pure Storage backend."""
        return PureStorageDestroyStep(
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
        """Create a configuration update step for Pure Storage backend."""
        return PureStorageUpdateConfigStep(
            deployment,
            self,
            backend_name,
            config_updates,
        )


# Pure Storage-specific step implementations using base step classes
class PureStorageDeployStep(BaseStorageBackendDeployStep):
    """Deploy Pure Storage backend using base step class."""

    def get_terraform_variables(self) -> Dict[str, Any]:
        """Get Terraform variables for Pure Storage backend deployment."""
        return self.backend_instance.get_terraform_variables(
            self.backend_name, self.backend_config, self.model
        )


class PureStorageDestroyStep(BaseStorageBackendDestroyStep):
    """Destroy Pure Storage backend using base step class."""


class PureStorageUpdateConfigStep(BaseStorageBackendConfigUpdateStep):
    """Update Pure Storage backend configuration using base step class."""
