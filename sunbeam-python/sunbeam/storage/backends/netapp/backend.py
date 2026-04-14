# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""NetApp ONTAP backend implementation using base step classes."""

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


class Family(StrEnum):
    """Enumeration of valid storage family types."""

    ONTAP_CLUSTER = "ontap_cluster"


class TransportType(StrEnum):
    """Enumeration of valid transport types."""

    HTTP = "http"
    HTTPS = "https"


class LunSpaceReservation(StrEnum):
    """Enumeration of valid LUN space reservation options."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class NetAppConfig(StorageBackendConfig):
    """Configuration model for NetApp ONTAP backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname.")
    ]

    # Protocol selection
    protocol: Annotated[
        Literal["iscsi", "nvme"],
        Field(description="Protocol selector: iscsi, nvme."),
    ]

    # Optional backend configuration
    netapp_storage_family: Annotated[
        Family | None,
        Field(description="The storage family type used on the storage system."),
    ] = None

    netapp_storage_protocol: Annotated[
        Literal["iscsi", "fc", "nfs", "nvme"] | None,
        Field(
            description="The storage protocol to be used on the data path with the storage system."
        ),
    ] = None

    netapp_server_hostname: Annotated[
        str | None,
        Field(
            description="The hostname (or IP address) for the storage system or proxy server."
        ),
    ] = None

    netapp_server_port: Annotated[
        int | None,
        Field(
            description="The TCP port to use for communication with the storage system or proxy server."
        ),
    ] = None

    netapp_use_legacy_client: Annotated[
        bool | None,
        Field(
            description="Select which ONTAP client to use for retrieving and modifying data on the storage."
        ),
    ] = None

    netapp_async_rest_timeout: Annotated[
        int | None,
        Field(
            description="The maximum time in seconds to wait for completing a REST asynchronous operation."
        ),
    ] = None

    netapp_transport_type: Annotated[
        TransportType | None,
        Field(
            description="The transport protocol used when communicating with the storage system or proxy server."
        ),
    ] = None

    netapp_ssl_cert_path: Annotated[
        str | None,
        Field(
            description="The path to a CA_BUNDLE file or directory with certificates of trusted CA."
        ),
    ] = None

    netapp_login: Annotated[
        str | None,
        Field(
            description="Administrative user account name used to access the storage system or proxy server."
        ),
    ] = None

    netapp_password: Annotated[
        str | None,
        Field(
            description="Password for the administrative user account specified in the netapp_login option."
        ),
        SecretDictField(field="netapp-password"),
    ] = None

    netapp_private_key_file: Annotated[
        str | None,
        Field(
            description="Absolute path to the file containing the private key associated with the certificate."
        ),
        SecretDictField(field="netapp-private-key-file"),
    ] = None

    netapp_certificate_file: Annotated[
        str | None,
        Field(
            description="Absolute path to the file containing the digital certificate."
        ),
        SecretDictField(field="netapp-certificate-file"),
    ] = None

    netapp_ca_certificate_file: Annotated[
        str,
        Field(
            description="Absolute path to the file containing the public key certificate of the trusted CA."
        ),
        SecretDictField(field="netapp-ca-certificate-file"),
    ]

    netapp_certificate_host_validation: Annotated[
        bool | None,
        Field(description="Enable certificate verification for host validation."),
    ] = None

    netapp_size_multiplier: Annotated[
        str | None,
        Field(
            description="Multiplier for requested volume size to ensure enough space."
        ),
    ] = None

    netapp_lun_space_reservation: Annotated[
        LunSpaceReservation | None,
        Field(
            description="Determines if storage space is reserved for LUN allocation."
        ),
    ] = None

    netapp_driver_reports_provisioned_capacity: Annotated[
        bool | None,
        Field(
            description="Enable querying of storage system to calculate volumes provisioned size."
        ),
    ] = None

    netapp_vserver: Annotated[
        str | None,
        Field(
            description="Virtual storage server (Vserver) name on the storage cluster."
        ),
    ] = None

    netapp_disaggregated_platform: Annotated[
        bool | None,
        Field(description="Enable ASA r2 workflows for NetApp disaggregated platform."),
    ] = None

    netapp_nfs_image_cache_cleanup_interval: Annotated[
        int | None,
        Field(description="Time in seconds between NFS image cache cleanup tasks."),
    ] = None

    thres_avl_size_perc_start: Annotated[
        int | None,
        Field(
            description="Percentage of available space for an NFS share to trigger cache cleaning."
        ),
    ] = None

    thres_avl_size_perc_stop: Annotated[
        int | None,
        Field(
            description="Percentage of available space on an NFS share to stop cache cleaning."
        ),
    ] = None

    expiry_thres_minutes: Annotated[
        int | None,
        Field(
            description="Threshold for last access time for images in the NFS image cache."
        ),
    ] = None

    netapp_lun_ostype: Annotated[
        str | None,
        Field(
            description="Type of operating system that will access a LUN exported from Data ONTAP."
        ),
    ] = None

    netapp_namespace_ostype: Annotated[
        str | None,
        Field(
            description="Type of operating system that will access a namespace exported from Data ONTAP."
        ),
    ] = None

    netapp_host_type: Annotated[
        str | None,
        Field(
            description="Type of operating system for all initiators that can access a LUN."
        ),
    ] = None

    netapp_pool_name_search_pattern: Annotated[
        str | None,
        Field(
            description="Regular expression to restrict provisioning to specified pools."
        ),
    ] = None

    netapp_lun_clone_busy_timeout: Annotated[
        int | None,
        Field(
            description="Maximum time to retry LUN clone operation when device busy error occurs."
        ),
    ] = None

    netapp_lun_clone_busy_interval: Annotated[
        int | None,
        Field(
            description="Time interval to retry LUN clone operation when device busy error occurs."
        ),
    ] = None

    netapp_dedupe_cache_expiry_duration: Annotated[
        int | None,
        Field(
            description="Time interval between updates of netapp_dedupe_used_percent for ONTAP backend pools."
        ),
    ] = None

    netapp_performance_cache_expiry_duration: Annotated[
        int | None,
        Field(
            description="Time interval between updates of performance utilization for ONTAP backend pools."
        ),
    ] = None

    netapp_replication_aggregate_map: Annotated[
        str | None,
        Field(
            description="Aggregate mapping between source and destination back ends for replication."
        ),
    ] = None

    netapp_snapmirror_quiesce_timeout: Annotated[
        int | None,
        Field(
            description="Maximum time to wait for existing SnapMirror transfers to complete before aborting."
        ),
    ] = None

    netapp_replication_volume_online_timeout: Annotated[
        int | None,
        Field(
            description="Time to wait for a replication volume create to complete and go online."
        ),
    ] = None

    netapp_replication_policy: Annotated[
        str | None,
        Field(
            description="Replication policy to be used while creating snapmirror relationship."
        ),
    ] = None

    netapp_api_trace_pattern: Annotated[
        str | None,
        Field(description="Regular expression to limit the API tracing."),
    ] = None

    netapp_migrate_volume_timeout: Annotated[
        int | None,
        Field(
            description="Time to wait for storage assisted volume migration to complete."
        ),
    ] = None


class NetAppBackend(StorageBackendBase):
    """NetApp ONTAP backend implementation."""

    backend_type = "netapp"
    display_name = "NetApp ONTAP"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-netapp"

    @property
    def charm_channel(self) -> str:
        """Return the default charm channel."""
        return "latest/edge"

    @property
    def charm_revision(self) -> str | None:
        """Return a pinned charm revision, if any."""
        return None

    @property
    def charm_base(self) -> str:
        """Return the target base for this charm."""
        return "ubuntu@24.04"

    @property
    def supports_ha(self) -> bool:
        """Whether this backend supports HA deployments."""
        return True

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration model type for this backend."""
        return NetAppConfig
