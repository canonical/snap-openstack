# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Dell Storage Center storage backend implementation using base step classes."""

import logging
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from rich.console import Console

from sunbeam.core.manifest import Manifest, StorageBackendConfig
from sunbeam.core.deployment import Deployment
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class DellSCConfig(StorageBackendConfig):
    """Static configuration model for Dell Storage Center storage backend.

    This model includes all configuration options supported by the
    cinder-volume-dellsc charm as defined in charmcraft.yaml.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Dell Storage Center management IP or hostname")
    ]
    san_username: Annotated[
        str,
        Field(description="SAN management username"),
        SecretDictField(field="primary-username"),
    ]
    san_password: Annotated[
        str,
        Field(description="SAN management password"),
        SecretDictField(field="primary-password"),
    ]
    dell_sc_ssn: Annotated[
        int | None, Field(description="Storage Center System Serial Number")
    ] = None
    protocol: Annotated[
        Literal["fc", "iscsi"] | None,
        Field(description="Front-end protocol (fc or iscsi)"),
    ] = None

    # Backend configuration
    volume_backend_name: Annotated[
        str | None, Field(description="Name that Cinder will report for this backend")
    ] = None
    backend_availability_zone: Annotated[
        str | None,
        Field(description="Availability zone to associate with this backend"),
    ] = None

    # Dell Storage Center specific options
    dell_sc_api_port: Annotated[
        int | None, Field(description="Dell Storage Center API port")
    ] = None
    dell_sc_server_folder: Annotated[
        str | None, Field(description="Server folder name on Dell SC")
    ] = None
    dell_sc_volume_folder: Annotated[
        str | None, Field(description="Volume folder name on Dell SC")
    ] = None
    dell_server_os: Annotated[
        str | None, Field(description="Server OS type for Dell SC")
    ] = None
    dell_sc_verify_cert: Annotated[
        bool | None, Field(description="Verify SSL certificate for Dell SC API")
    ] = None

    # Provisioning options
    san_thin_provision: Annotated[
        bool | None, Field(description="Enable thin provisioning")
    ] = None

    # Domain and network filtering
    excluded_domain_ips: Annotated[
        str | None, Field(description="Comma-separated list of excluded domain IPs")
    ] = None
    included_domain_ips: Annotated[
        str | None, Field(description="Comma-separated list of included domain IPs")
    ] = None

    # Dual DSM configuration
    secondary_san_ip: Annotated[
        str | None, Field(description="Secondary Dell Storage Center management IP")
    ] = None
    secondary_san_username: Annotated[
        str | None,
        Field(description="Secondary SAN management username"),
        SecretDictField(field="secondary-username"),
    ] = None
    secondary_san_password: Annotated[
        str | None,
        Field(description="Secondary SAN management password"),
        SecretDictField(field="secondary-password"),
    ] = None
    secondary_sc_api_port: Annotated[
        int | None, Field(description="Secondary Dell Storage Center API port")
    ] = None

    # API timeout configuration
    dell_api_async_rest_timeout: Annotated[
        int | None, Field(description="Async REST API timeout in seconds")
    ] = None
    dell_api_sync_rest_timeout: Annotated[
        int | None, Field(description="Sync REST API timeout in seconds")
    ] = None

    # SSH connection settings
    ssh_conn_timeout: Annotated[
        int | None, Field(description="SSH connection timeout in seconds")
    ] = None
    ssh_max_pool_conn: Annotated[
        int | None, Field(description="Maximum SSH pool connections")
    ] = None
    ssh_min_pool_conn: Annotated[
        int | None, Field(description="Minimum SSH pool connections")
    ] = None

    @model_validator(mode="after")
    def _validate_secondary_credentials(self) -> "DellSCConfig":
        if self.secondary_san_ip and (
            not self.secondary_san_username or not self.secondary_san_password
        ):
            raise ValueError(
                "secondary-san-username and secondary-san-password are required "
                "when secondary-san-ip is set"
            )
        return self


class DellSCBackend(StorageBackendBase):
    """Dell Storage Center storage backend implementation."""

    backend_type = "dellsc"
    display_name = "Dell Storage Center"

    @property
    def charm_name(self) -> str:
        """Return the charm name for this backend."""
        return "cinder-volume-dellsc"

    @property
    def charm_channel(self) -> str:
        """Return the charm channel for this backend."""
        return "latest/edge"

    @property
    def charm_revision(self) -> str | None:
        """Return the charm revision for this backend."""
        return None

    @property
    def charm_base(self) -> str:
        """Return the charm base for this backend."""
        return "ubuntu@24.04"

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration class for Dell Storage Center backend."""
        return DellSCConfig

    def build_terraform_vars(
        self,
        deployment: Deployment,
        manifest: Manifest,
        backend_name: str,
        config: StorageBackendConfig,
    ) -> dict[str, Any]:
        """Generate Terraform variables for Dell SC backend deployment."""
        config_dict = config.model_dump(exclude_none=True, by_alias=True)

        secrets: dict[str, dict[str, Any]] = {}

        primary_username = config_dict.pop("san-username", None)
        primary_password = config_dict.pop("san-password", None)
        secondary_username = config_dict.pop("secondary-san-username", None)
        secondary_password = config_dict.pop("secondary-san-password", None)

        if (
            primary_username is not None
            or primary_password is not None
            or secondary_username is not None
            or secondary_password is not None
        ):
            secret: dict[str, Any] = {}
            if primary_username is not None:
                secret["primary-username"] = primary_username
            if primary_password is not None:
                secret["primary-password"] = primary_password
            if secondary_username is not None:
                secret["secondary-username"] = secondary_username
            if secondary_password is not None:
                secret["secondary-password"] = secondary_password
            secrets["dellsc-config-secret"] = secret

        charm_channel = self.charm_channel
        charm_revision = None
        if backends_cfg := manifest.storage.root.get(self.backend_type):
            if backend_cfg := backends_cfg.root.get(backend_name):
                if charm_cfg := backend_cfg.software.charms.get(self.charm_name):
                    if channel := charm_cfg.channel:
                        charm_channel = channel
                    if revision := charm_cfg.revision:
                        charm_revision = revision

        return {
            "principal_application": self.principal_application,
            "charm_name": self.charm_name,
            "charm_base": self.charm_base,
            "charm_channel": charm_channel,
            "charm_revision": charm_revision,
            "endpoint_bindings": self.get_endpoint_bindings(deployment),
            "charm_config": config_dict,
            "secrets": secrets,
        }
