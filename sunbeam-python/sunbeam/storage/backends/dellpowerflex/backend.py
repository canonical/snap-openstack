# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Dell PowerFlex backend implementation using base step classes."""

import logging
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class Protocol(StrEnum):
    """Enumeration of valid PowerFlex protocol types."""

    SCALEIO = "scaleio"
    NVME_TCP = "nvme-tcp"


class PowerFlexConfig(StorageBackendConfig):
    """Configuration model for Dell PowerFlex backend.

    This model includes the essential configuration options for deploying
    a Dell PowerFlex backend. Additional configuration can be managed dynamically
    through the charm configuration system.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="IP address of the PowerFlex Gateway server")
    ]
    san_login: Annotated[
        str,
        Field(
            description="Username for PowerFlex Gateway with administrative privileges"
        ),
        SecretDictField(field="san-login"),
    ]
    san_password: Annotated[
        str,
        Field(description="Password for PowerFlex Gateway authentication"),
        SecretDictField(field="san-password"),
    ]
    powerflex_storage_pools: Annotated[
        str,
        Field(
            description="Comma-separated list of storage pools in format "
            "protection_domain_name:storage_pool_name"
        ),
    ]

    # Protocol selection
    protocol: Annotated[
        Literal["scaleio", "nvme-tcp"],
        Field(
            description="Dell PowerFlex protocol selector: scaleio (PowerFlexDriver) or nvme-tcp (PowerFlexNVMeDriver)"
        ),
    ] = "scaleio"

    # PowerFlex Gateway configuration
    powerflex_rest_server_port: Annotated[
        int | None,
        Field(description="Secured port to use when connecting to PowerFlex Manager"),
    ] = 443

    # Storage management
    powerflex_max_over_subscription_ratio: Annotated[
        float | None,
        Field(description="Maximum oversubscription ratio allowed"),
    ] = 10.0

    # Volume management
    powerflex_round_volume_capacity: Annotated[
        bool | None,
        Field(
            description="Round volume sizes up to 8GB boundaries. "
            "PowerFlex OS requires volumes to be sized in multiples of 8GB"
        ),
    ] = True

    # Advanced configuration
    powerflex_allow_non_padded_volumes: Annotated[
        bool | None,
        Field(
            description="Allow volumes to be created in Storage Pools when zero padding "
            "is disabled. Should not be enabled if multiple tenants use volumes "
            "from a shared Storage Pool"
        ),
    ] = None
    powerflex_allow_migration_during_rebuild: Annotated[
        bool | None,
        Field(description="Allow volume migration during rebuild operation"),
    ] = None

    # REST API timeouts
    rest_api_connect_timeout: Annotated[
        int | None,
        Field(description="Connection timeout value (in seconds) for REST calls"),
    ] = 30
    rest_api_read_timeout: Annotated[
        int | None,
        Field(description="Read timeout value (in seconds) for REST calls"),
    ] = 30

    # Image cache configuration
    powerflex_max_image_cache_vtree_size: Annotated[
        int | None,
        Field(
            description="Maximum size of the vTree associated with an entry in the "
            "image volume cache"
        ),
    ] = 0

    # Performance options
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations"),
    ] = None

    # Replication configuration
    replication_device: Annotated[
        str | None,
        Field(
            description="Specific replication configuration settings. Must be set under "
            "the form of backend_id:powerflex_repl, san_ip: <Replication system San ip>, "
            "san_login: <Replication system San username>, san_password: <Replication system San password>"
        ),
    ] = None

    # SSL and security
    driver_ssl_cert: Annotated[
        str | None,
        Field(description="PEM-encoded SSL certificate to use for HTTPS connections")
    ] = None

    # Standard Cinder options
    backend_availability_zone: Annotated[
        str | None,
        Field(description="Availability zone to associate with this backend"),
    ] = None
    volume_backend_name: Annotated[
        str | None,
        Field(description="Name that Cinder will report for this backend"),
    ] = None


class PowerFlexBackend(StorageBackendBase):
    """Dell PowerFlex backend implementation."""

    backend_type = "dellpowerflex"
    display_name = "Dell PowerFlex"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm name for this backend."""
        return "cinder-volume-dellpowerflex"

    @property
    def charm_channel(self) -> str:
        """Return the charm channel for this backend."""
        return "latest/stable"

    @property
    def charm_revision(self) -> str | None:
        """Return the charm revision for this backend."""
        return None

    @property
    def charm_base(self) -> str:
        """Return the charm base for this backend."""
        return "ubuntu@24.04"

    @property
    def supports_ha(self) -> bool:
        """Return whether this backend supports HA deployments."""
        return True

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration class for Dell PowerFlex backend."""
        return PowerFlexConfig
