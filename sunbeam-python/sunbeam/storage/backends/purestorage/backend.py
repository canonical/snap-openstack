# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Pure Storage FlashArray backend implementation using base step classes."""

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


class Personality(StrEnum):
    """Enumeration of valid host personality types."""

    AIX = "aix"
    ESXI = "esxi"
    HITACHI_VSP = "hitachi-vsp"
    HPUX = "hpux"
    ORACLE_VM_SERVER = "oracle-vm-server"
    SOLARIS = "solaris"
    VMS = "vms"


class PureStorageConfig(StorageBackendConfig):
    """Configuration model for Pure Storage FlashArray backend.

    This model includes the essential configuration options for deploying
    a Pure Storage backend. Additional configuration can be managed dynamically
    through the charm configuration system.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Pure Storage FlashArray management IP or hostname")
    ]
    pure_api_token: Annotated[
        str,
        Field(description="REST API authorization token from FlashArray"),
        SecretDictField(field="token"),
    ]

    # Optional backend configuration
    protocol: Annotated[
        Literal["iscsi", "fc", "nvme"] | None,
        Field(description="Pure Storage protocol (iscsi, fc, nvme)"),
    ] = None
    # Protocol-specific options
    pure_iscsi_cidr: Annotated[
        str | None,
        Field(
            description="CIDR of FlashArray iSCSI targets hosts can connect to",
        ),
    ] = None
    pure_iscsi_cidr_list: Annotated[
        str | None,
        Field(description="Comma-separated list of CIDR for iSCSI targets"),
    ] = None
    pure_nvme_cidr: Annotated[
        str | None,
        Field(description="CIDR of FlashArray NVMe targets hosts can connect to"),
    ] = None

    pure_nvme_cidr_list: Annotated[
        str | None,
        Field(description="Comma-separated list of CIDR for NVMe targets"),
    ] = None
    pure_nvme_transport: Annotated[
        Literal["tcp"] | None,  # note(gboutry): roce not supported yet
        Field(description="NVMe transport layer"),
    ] = None

    # Host and protocol tuning
    pure_host_personality: Annotated[
        Personality | None, Field(description="Host personality for protocol tuning")
    ] = None

    # Storage management
    pure_automatic_max_oversubscription_ratio: Annotated[
        bool | None,
        Field(description="Automatically determine oversubscription ratio"),
    ] = None
    pure_eradicate_on_delete: Annotated[
        bool | None,
        Field(
            description="Immediately eradicate volumes on delete "
            "(WARNING: not recoverable)",
        ),
    ] = None

    # Replication settings
    pure_replica_interval_default: Annotated[
        int | None, Field(description="Snapshot replication interval in seconds")
    ] = None
    pure_replica_retention_short_term_default: Annotated[
        int | None,
        Field(description="Retain all snapshots on target for this time (seconds)"),
    ] = None
    pure_replica_retention_long_term_per_day_default: Annotated[
        int | None, Field(description="Retain how many snapshots for each day")
    ] = None
    pure_replica_retention_long_term_default: Annotated[
        int | None,
        Field(description="Retain snapshots per day on target for this time (days)"),
    ] = None
    pure_replication_pg_name: Annotated[
        str | None,
        Field(
            description="Pure Protection Group name for async replication",
        ),
    ] = None
    pure_replication_pod_name: Annotated[
        str | None,
        Field(description="Pure Pod name for sync replication"),
    ] = None

    # Advanced replication
    pure_trisync_enabled: Annotated[
        bool | None,
        Field(description="Enable 3-site replication (sync + async)"),
    ] = None
    pure_trisync_pg_name: Annotated[
        str | None,
        Field(description="Protection Group name for trisync replication"),
    ] = None

    # SSL and security
    driver_ssl_cert: Annotated[
        str | None, Field(description="SSL certificate content in PEM format")
    ] = None

    # Performance options
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(
            description="Enable multipathing for image transfer operations",
        ),
    ] = None


class PureStorageBackend(StorageBackendBase):
    """Pure Storage FlashArray backend implementation."""

    backend_type = "purestorage"
    display_name = "Pure Storage FlashArray"

    @property
    def charm_name(self) -> str:
        """Return the charm name for this backend."""
        return "cinder-volume-purestorage"

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
        """Return the configuration class for Pure Storage backend."""
        return PureStorageConfig
