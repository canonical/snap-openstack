# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Hitachi VSP storage backend implementation using base step classes."""

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


class HitachiConfig(StorageBackendConfig):
    """Static configuration model for Hitachi VSP storage backend.

    This model includes all configuration options supported by the
    cinder-volume-hitachi charm as defined in charmcraft.yaml.
    """

    # Required fields (inherited from StorageBackendConfig)
    # name: str (from base class)

    # Mandatory connection parameters
    hitachi_storage_id: str = Field(
        ..., description="Storage system product number/serial"
    )
    hitachi_pools: str = Field(
        ..., description="Comma-separated list of DP pool names/IDs"
    )
    san_ip: str = Field(..., description="Hitachi VSP management IP or hostname")
    san_username: str = Field(..., description="SAN management username")
    san_password: str = Field(..., description="SAN management password")
    protocol: Literal["FC", "iSCSI"] = Field(
        ..., description="Front-end protocol (FC or iSCSI)"
    )

    # Backend configuration
    volume_backend_name: str = Field(
        default="", description="Name that Cinder will report for this backend"
    )
    backend_availability_zone: str = Field(
        default="", description="Availability zone to associate with this backend"
    )

    # Optional host-group / zoning controls
    hitachi_target_ports: str = Field(
        default="", description="Comma-separated front-end port labels"
    )
    hitachi_compute_target_ports: str = Field(
        default="", description="Comma-separated compute-node port IDs"
    )
    hitachi_ldev_range: str = Field(
        default="", description="LDEV range usable by the driver"
    )
    hitachi_zoning_request: bool = Field(
        default=False, description="Request FC zone-manager to create zoning"
    )

    # Copy & replication tuning
    hitachi_copy_speed: int = Field(
        default=3, description="Copy bandwidth throttle (1-15)"
    )
    hitachi_copy_check_interval: int = Field(
        default=3, description="Seconds between sync copy-status polls"
    )
    hitachi_async_copy_check_interval: int = Field(
        default=10, description="Seconds between async copy-status polls"
    )

    # iSCSI authentication
    use_chap_auth: bool = Field(
        default=False, description="Use CHAP authentication for iSCSI"
    )

    # Array ranges and controls
    hitachi_discard_zero_page: bool = Field(
        default=True, description="Enable zero-page reclamation in DP-VOLs"
    )
    hitachi_exec_retry_interval: int = Field(
        default=5, description="Seconds to wait before retrying REST API call"
    )
    hitachi_extend_timeout: int = Field(
        default=600, description="Max seconds to wait for volume extension"
    )
    hitachi_group_create: bool = Field(
        default=False, description="Automatically create host groups or iSCSI targets"
    )
    hitachi_group_delete: bool = Field(
        default=False, description="Automatically delete unused host groups"
    )
    hitachi_group_name_format: str = Field(
        default="", description="Python format string for naming host groups"
    )
    hitachi_host_mode_options: str = Field(
        default="", description="Comma-separated host mode options"
    )
    hitachi_lock_timeout: int = Field(
        default=7200, description="Max seconds for array login/unlock operations"
    )
    hitachi_lun_retry_interval: int = Field(
        default=1, description="Seconds before retrying LUN mapping"
    )
    hitachi_lun_timeout: int = Field(
        default=50, description="Max seconds to wait for LUN mapping"
    )
    hitachi_port_scheduler: bool = Field(
        default=False, description="Enable round-robin WWN registration"
    )

    # Mirror/replication settings
    hitachi_mirror_compute_target_ports: str = Field(
        default="", description="Compute-node port names for GAD"
    )
    hitachi_mirror_ldev_range: str = Field(
        default="", description="LDEV range for secondary storage"
    )
    hitachi_mirror_pair_target_number: int = Field(
        default=0, description="Host group number for GAD on secondary"
    )
    hitachi_mirror_pool: str = Field(
        default="", description="DP pool name/ID on secondary storage"
    )
    hitachi_mirror_rest_api_ip: str = Field(
        default="", description="REST API IP on secondary storage"
    )
    hitachi_mirror_rest_api_port: int = Field(
        default=443, description="REST API port on secondary storage"
    )
    hitachi_mirror_rest_pair_target_ports: str = Field(
        default="", description="Pair-target port names for GAD"
    )
    hitachi_mirror_snap_pool: str = Field(
        default="", description="Snapshot pool on secondary storage"
    )
    hitachi_mirror_ssl_cert_path: str = Field(
        default="", description="CA_BUNDLE for secondary REST endpoint"
    )
    hitachi_mirror_ssl_cert_verify: bool = Field(
        default=False, description="Validate SSL cert of secondary REST"
    )
    hitachi_mirror_storage_id: str = Field(
        default="", description="Product number of secondary storage"
    )
    hitachi_mirror_target_ports: str = Field(
        default="", description="Controller node port IDs for GAD"
    )
    hitachi_mirror_use_chap_auth: bool = Field(
        default=False, description="Use CHAP auth for GAD on secondary"
    )

    # Replication settings
    hitachi_pair_target_number: int = Field(
        default=0, description="Host group number for primary replication"
    )
    hitachi_path_group_id: int = Field(
        default=0, description="Path group ID for remote replication"
    )
    hitachi_quorum_disk_id: int = Field(
        default=0, description="Quorum disk ID for Global-Active Device"
    )
    hitachi_replication_copy_speed: int = Field(
        default=3, description="Copy speed for remote replication"
    )
    hitachi_replication_number: int = Field(
        default=0, description="Instance number for REST API on replication"
    )
    hitachi_replication_status_check_long_interval: int = Field(
        default=600, description="Poll interval after initial check"
    )
    hitachi_replication_status_check_short_interval: int = Field(
        default=5, description="Initial poll interval"
    )
    hitachi_replication_status_check_timeout: int = Field(
        default=86400, description="Max seconds for status change"
    )

    # REST API settings
    hitachi_rest_another_ldev_mapped_retry_timeout: int = Field(
        default=600, description="Retry seconds when LDEV allocation fails"
    )
    hitachi_rest_connect_timeout: int = Field(
        default=30, description="Max seconds to establish REST connection"
    )
    hitachi_rest_disable_io_wait: bool = Field(
        default=True, description="Detach volumes without waiting for I/O drain"
    )
    hitachi_rest_get_api_response_timeout: int = Field(
        default=1800, description="Max seconds for sync REST GET"
    )
    hitachi_rest_job_api_response_timeout: int = Field(
        default=1800, description="Max seconds for async REST PUT/DELETE"
    )
    hitachi_rest_keep_session_loop_interval: int = Field(
        default=180, description="Seconds between keep-alive loops"
    )
    hitachi_rest_pair_target_ports: str = Field(
        default="", description="Pair-target port names for REST operations"
    )
    hitachi_rest_server_busy_timeout: int = Field(
        default=7200, description="Max seconds when REST API returns busy"
    )
    hitachi_rest_tcp_keepalive: bool = Field(
        default=True, description="Enable TCP keepalive for REST connections"
    )
    hitachi_rest_tcp_keepcnt: int = Field(
        default=4, description="Number of TCP keepalive probes"
    )
    hitachi_rest_tcp_keepidle: int = Field(
        default=60, description="Seconds before sending first TCP keepalive"
    )
    hitachi_rest_tcp_keepintvl: int = Field(
        default=15, description="Seconds between TCP keepalive probes"
    )
    hitachi_rest_timeout: int = Field(
        default=30, description="Max seconds for each REST API call"
    )
    hitachi_restore_timeout: int = Field(
        default=86400, description="Max seconds to wait for restore operation"
    )

    # Snapshot settings
    hitachi_snap_pool: str = Field(default="", description="Pool name/ID for snapshots")
    hitachi_state_transition_timeout: int = Field(
        default=900, description="Max seconds for volume state transition"
    )

    # Juju secrets for credentials (not charm config options)
    san_credentials_secret: str = Field(
        default="", description="Juju secret URI for SAN credentials"
    )
    chap_credentials_secret: str = Field(
        default="", description="Juju secret URI for CHAP credentials"
    )
    hitachi_mirror_chap_credentials_secret: str = Field(
        default="", description="Juju secret URI for mirror CHAP credentials"
    )
    hitachi_mirror_rest_credentials_secret: str = Field(
        default="", description="Juju secret URI for mirror REST credentials"
    )

    chap_username: str = Field(
        default="", description="CHAP username for secret creation"
    )
    chap_password: str = Field(
        default="", description="CHAP password for secret creation"
    )
    hitachi_mirror_chap_username: str = Field(
        default="", description="Mirror CHAP username for secret creation"
    )
    hitachi_mirror_chap_password: str = Field(
        default="", description="Mirror CHAP password for secret creation"
    )
    hitachi_mirror_rest_username: str = Field(
        default="", description="Mirror REST username for secret creation"
    )
    hitachi_mirror_rest_password: str = Field(
        default="", description="Mirror REST password for secret creation"
    )


class HitachiBackend(StorageBackendBase):
    """Hitachi storage backend implementation."""

    name = "hitachi"
    display_name = "Hitachi VSP Storage"
    charm_name = "cinder-volume-hitachi"

    def __init__(self):
        """Initialize Hitachi backend."""
        super().__init__()
        self.tfplan = "hitachi-backend-plan"
        self.tfplan_dir = "deploy-hitachi-backend"

    charm_channel = (
        "latest/edge"  # Use edge for development, change to stable for production
    )
    charm_revision = 2
    charm_base = "ubuntu@24.04"  # Updated to match uploaded charm base
    backend_endpoint = "cinder-volume"
    units = 1
    additional_integrations = []

    @property
    def config_class(self) -> type[StorageBackendConfig]:
        """Return the configuration class for Hitachi backend."""
        return HitachiConfig

    def _get_credential_fields(self) -> Set[str]:
        """Get set of credential field names that should be excluded from charm config.

        For Hitachi backend, we exclude all credential fields and secret URIs.
        """
        return {
            # meta
            "name",
            # primary array credentials
            "san_username",
            "san_password",
            # CHAP credentials
            "chap_username",
            "chap_password",
            # mirror CHAP credentials
            "hitachi_mirror_chap_username",
            "hitachi_mirror_chap_password",
            # mirror REST credentials
            "hitachi_mirror_rest_username",
            "hitachi_mirror_rest_password",
            # juju secret URIs
            "san_credentials_secret",
            "chap_credentials_secret",
            "hitachi_mirror_chap_credentials_secret",
            "hitachi_mirror_rest_credentials_secret",
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

    # CLI registration uses base class implementation

    def get_terraform_variables(
        self, backend_name: str, config: StorageBackendConfig, model: str
    ) -> Dict[str, Any]:
        """Generate Terraform variables for Hitachi backend deployment."""
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
            "charm_hitachi_name": self.charm_name,
            "charm_hitachi_base": self.charm_base,
            "charm_hitachi_channel": self.charm_channel,
            "charm_hitachi_endpoint": self.backend_endpoint,
            "charm_hitachi_revision": self.charm_revision,
            "hitachi_backends": {
                backend_name: {
                    "charm_config": charm_config,
                    # Main array credentials (always required)
                    "san_username": config_dict.get("san_username", ""),
                    "san_password": config_dict.get("san_password", ""),
                    # CHAP credentials (optional)
                    "use_chap_auth": config_dict.get("use_chap_auth", False),
                    "chap_username": config_dict.get("chap_username", ""),
                    "chap_password": config_dict.get("chap_password", ""),
                    # Mirror CHAP credentials (optional)
                    "hitachi_mirror_chap_username": config_dict.get(
                        "hitachi_mirror_chap_username", ""
                    ),
                    "hitachi_mirror_chap_password": config_dict.get(
                        "hitachi_mirror_chap_password", ""
                    ),
                    # Mirror REST API credentials (optional)
                    "hitachi_mirror_rest_username": config_dict.get(
                        "hitachi_mirror_rest_username", ""
                    ),
                    "hitachi_mirror_rest_password": config_dict.get(
                        "hitachi_mirror_rest_password", ""
                    ),
                }
            },
        }

        return tfvars

    def _get_default_config(self) -> HitachiConfig:
        """Get a default configuration instance for comparison."""
        return HitachiConfig(
            name="dummy",
            hitachi_storage_id="dummy",
            hitachi_pools="dummy",
            san_ip="dummy",
            san_username="dummy",
            san_password="dummy",  # noqa: S106
            protocol="FC",
        )

    def prompt_for_config(self, backend_name: str) -> HitachiConfig:
        """Prompt user for Hitachi-specific configuration."""
        return self._prompt_for_config(backend_name)

    # IP/FQDN validation uses base class implementation

    def _prompt_for_config(self, backend_name: str) -> HitachiConfig:
        """Prompt user for Hitachi backend configuration."""
        console.print(
            "\n[bold blue]Hitachi VSP Storage Backend Configuration[/bold blue]"
        )
        console.print("Please provide the required configuration options:")

        # Prompt for required fields
        hitachi_storage_id = click.prompt("Array serial number", type=str)
        hitachi_pools = click.prompt("Storage pools (comma separated)", type=str)
        protocol = click.prompt(
            "Protocol",
            type=click.Choice(["FC", "iSCSI"], case_sensitive=False),
            default="FC",
        )
        san_ip = click.prompt(
            "Management IP/FQDN", type=str, value_proc=self._validate_ip_or_fqdn
        )

        # Main array credentials (will be automatically converted to Juju secret)
        console.print("\n[bold yellow]Array Credentials[/bold yellow]")
        console.print(
            "These credentials will be automatically stored in a Juju secret."
        )
        san_username = click.prompt("SAN username", type=str, default="maintenance")
        san_password = click.prompt("SAN password", type=str, hide_input=True)

        # Optional: prompt for volume backend name (defaults to backend name)
        volume_backend_name = click.prompt(
            "Volume backend name", type=str, default=backend_name, show_default=True
        )

        # Optional: CHAP authentication for iSCSI
        chap_username = ""
        chap_password = ""
        use_chap_auth = False
        if protocol.lower() == "iscsi":
            use_chap_auth = click.confirm(
                "\nUse CHAP authentication for iSCSI?", default=False
            )
            if use_chap_auth:
                console.print("[bold yellow]CHAP Credentials[/bold yellow]")
                console.print(
                    "These credentials will be automatically stored in a Juju secret."
                )
                chap_username = click.prompt("CHAP username", type=str)
                chap_password = click.prompt("CHAP password", type=str, hide_input=True)

        # Optional: Mirror/GAD configuration
        hitachi_mirror_chap_username = ""
        hitachi_mirror_chap_password = ""
        hitachi_mirror_rest_username = ""
        hitachi_mirror_rest_password = ""

        configure_mirror = click.confirm(
            "\nConfigure mirror/replication (GAD) settings?", default=False
        )
        if configure_mirror:
            console.print(
                "\n[bold yellow]Mirror/Replication Configuration[/bold yellow]"
            )

            # Mirror CHAP credentials
            if click.confirm("Configure mirror CHAP credentials?", default=False):
                console.print("[bold yellow]Mirror CHAP Credentials[/bold yellow]")
                console.print(
                    "These credentials will be automatically stored in a Juju secret."
                )
                hitachi_mirror_chap_username = click.prompt(
                    "Mirror CHAP username", type=str
                )
                hitachi_mirror_chap_password = click.prompt(
                    "Mirror CHAP password", type=str, hide_input=True
                )

            # Mirror REST API credentials
            if click.confirm("Configure mirror REST API credentials?", default=False):
                console.print("[bold yellow]Mirror REST API Credentials[/bold yellow]")
                console.print(
                    "These credentials will be automatically stored in a Juju secret."
                )
                hitachi_mirror_rest_username = click.prompt(
                    "Mirror REST API username", type=str
                )
                hitachi_mirror_rest_password = click.prompt(
                    "Mirror REST API password", type=str, hide_input=True
                )

        return HitachiConfig(
            name=backend_name,
            hitachi_storage_id=hitachi_storage_id,
            hitachi_pools=hitachi_pools,
            protocol=protocol,
            san_ip=san_ip,
            san_username=san_username,
            san_password=san_password,
            volume_backend_name=volume_backend_name,
            use_chap_auth=use_chap_auth,
            chap_username=chap_username,
            chap_password=chap_password,
            hitachi_mirror_chap_username=hitachi_mirror_chap_username,
            hitachi_mirror_chap_password=hitachi_mirror_chap_password,
            hitachi_mirror_rest_username=hitachi_mirror_rest_username,
            hitachi_mirror_rest_password=hitachi_mirror_rest_password,
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
        """Create a deployment step for Hitachi backend."""
        return HitachiDeployStep(
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
        """Create a destruction step for Hitachi backend."""
        return HitachiDestroyStep(
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
        """Create a configuration update step for Hitachi backend."""
        return HitachiUpdateConfigStep(
            deployment,
            self,
            backend_name,
            config_updates,
        )


# Hitachi-specific step implementations using base step classes
class HitachiDeployStep(BaseStorageBackendDeployStep):
    """Deploy Hitachi storage backend using base step class."""

    def get_terraform_variables(self) -> Dict[str, Any]:
        """Get Terraform variables for Hitachi backend deployment."""
        return self.backend_instance.get_terraform_variables(
            self.backend_name, self.backend_config, self.model
        )


class HitachiDestroyStep(BaseStorageBackendDestroyStep):
    """Destroy Hitachi storage backend using base step class."""


class HitachiUpdateConfigStep(BaseStorageBackendConfigUpdateStep):
    """Update Hitachi storage backend configuration using base step class."""
