# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Nexenta iSCSI backend implementation using base step classes."""

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


class RestProtocol(StrEnum):
    """Enumeration of valid REST protocol types."""

    HTTP = "http"
    HTTPS = "https"
    AUTO = "auto"


class DatasetCompression(StrEnum):
    """Enumeration of valid dataset compression types."""

    ON = "on"
    OFF = "off"
    GZIP = "gzip"
    GZIP_1 = "gzip-1"
    GZIP_2 = "gzip-2"
    GZIP_3 = "gzip-3"
    GZIP_4 = "gzip-4"
    GZIP_5 = "gzip-5"
    GZIP_6 = "gzip-6"
    GZIP_7 = "gzip-7"
    GZIP_8 = "gzip-8"
    GZIP_9 = "gzip-9"
    LZJB = "lzjb"
    ZLE = "zle"
    LZ4 = "lz4"


class DatasetDedup(StrEnum):
    """Enumeration of valid dataset deduplication types."""

    ON = "on"
    OFF = "off"
    SHA256 = "sha256"
    VERIFY = "verify"
    SHA256_VERIFY = "sha256, verify"


class NexentaConfig(StorageBackendConfig):
    """Configuration model for Nexenta iSCSI backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname")
    ]
    protocol: Annotated[
        Literal["iscsi"] | None,
        Field(description="Protocol selector: iscsi."),
    ] = None
    nexenta_rest_password: Annotated[
        str,
        Field(description="Password to connect to NexentaEdge"),
        SecretDictField(field="nexenta-rest-password"),
    ]
    nexenta_rest_protocol: Annotated[
        RestProtocol | None,
        Field(
            description="Use http or https for NexentaStor management REST API connection"  # noqa: E501
        ),
    ] = None
    nexenta_nbd_symlinks_dir: Annotated[
        str | None,
        Field(
            description="NexentaEdge logical path of directory to store symbolic links to NBDs"  # noqa: E501
        ),
    ] = None
    nexenta_rest_user: Annotated[
        str | None,
        Field(description="User name to connect to NexentaEdge"),
    ] = None
    nexenta_lun_container: Annotated[
        str | None,
        Field(description="NexentaEdge logical path of bucket for LUNs"),
    ] = None
    nexenta_iscsi_service: Annotated[
        str | None,
        Field(description="NexentaEdge iSCSI service name"),
    ] = None
    nexenta_iops_limit: Annotated[
        int | None,
        Field(description="NexentaEdge iSCSI LUN object IOPS limit"),
    ] = None
    nexenta_chunksize: Annotated[
        int | None,
        Field(description="NexentaEdge iSCSI LUN object chunk size"),
    ] = None
    nexenta_replication_count: Annotated[
        int | None,
        Field(description="NexentaEdge iSCSI LUN object replication count"),
    ] = None
    nexenta_host: Annotated[
        str | None,
        Field(description="IP address of NexentaStor Appliance"),
    ] = None
    nexenta_rest_connect_timeout: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the time limit (in seconds), within which the "
                "connection to NexentaStor management REST API server must be "
                "established"
            )
        ),
    ] = "30"
    nexenta_rest_read_timeout: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the time limit (in seconds), within which "
                "NexentaStor management REST API server must send a response"
            )
        ),
    ] = "300"
    nexenta_rest_backoff_factor: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the backoff factor to apply between connection "
                "attempts to NexentaStor management REST API server"
            )
        ),
    ] = "0.5"
    nexenta_rest_retry_count: Annotated[
        int | None,
        Field(
            description=(
                "Specifies the number of times to repeat NexentaStor "
                "management REST API call in case of connection errors and "
                "NexentaStor appliance EBUSY or ENOENT errors"
            )
        ),
    ] = 3
    nexenta_use_https: Annotated[
        bool | None,
        Field(
            description=(
                "Use HTTP secure protocol for NexentaStor management REST API "
                "connections"
            )
        ),
    ] = True
    nexenta_lu_writebackcache_disabled: Annotated[
        bool | None,
        Field(description="Postponed write to backing store or not"),
    ] = False
    nexenta_iscsi_target_portal_groups: Annotated[
        str | None,
        Field(description="NexentaStor target portal groups"),
    ] = None
    nexenta_iscsi_target_portals: Annotated[
        str | None,
        Field(
            description=(
                "Comma separated list of portals for NexentaStor5, in format "
                "of IP1:port1,IP2:port2. Port is optional, default=3260. "
                "Example: 10.10.10.1:3267,10.10.1.2"
            )
        ),
    ] = None
    nexenta_iscsi_target_host_group: Annotated[
        str | None,
        Field(description="Group of hosts which are allowed to access volumes"),
    ] = "all"
    nexenta_iscsi_target_portal_port: Annotated[
        int | None,
        Field(description="Nexenta appliance iSCSI target portal port"),
    ] = 3260
    nexenta_luns_per_target: Annotated[
        int | None,
        Field(description="Amount of LUNs per iSCSI target"),
    ] = 100
    nexenta_volume: Annotated[
        str | None,
        Field(description="NexentaStor pool name that holds all volumes"),
    ] = "cinder"
    nexenta_target_prefix: Annotated[
        str | None,
        Field(description="iqn prefix for NexentaStor iSCSI targets"),
    ] = "iqn.1986-03.com.sun:02:cinder"
    nexenta_target_group_prefix: Annotated[
        str | None,
        Field(description="Prefix for iSCSI target groups on NexentaStor"),
    ] = "cinder"
    nexenta_host_group_prefix: Annotated[
        str | None,
        Field(description="Prefix for iSCSI host groups on NexentaStor"),
    ] = "cinder"
    nexenta_volume_group: Annotated[
        str | None,
        Field(description="Volume group for NexentaStor5 iSCSI"),
    ] = "iscsi"
    nexenta_shares_config: Annotated[
        str | None,
        Field(description="File with the list of available nfs shares"),
    ] = "/etc/cinder/nfs_shares"
    nexenta_mount_point_base: Annotated[
        str | None,
        Field(description="Base directory that contains NFS share mount points"),
    ] = "$state_path/mnt"
    nexenta_sparsed_volumes: Annotated[
        bool | None,
        Field(
            description=(
                "Enables or disables the creation of volumes as sparsed files "
                "that take no space. If disabled (False), volume is created as "
                "a regular file, which takes a long time."
            )
        ),
    ] = True
    nexenta_qcow2_volumes: Annotated[
        bool | None,
        Field(description="Create volumes as QCOW2 files rather than raw files"),
    ] = False
    nexenta_nms_cache_volroot: Annotated[
        bool | None,
        Field(
            description="If set True cache NexentaStor appliance volroot option value."
        ),
    ] = True
    nexenta_dataset_compression: Annotated[
        DatasetCompression | None,
        Field(description="Compression value for new ZFS folders."),
    ] = None
    nexenta_dataset_dedup: Annotated[
        DatasetDedup | None,
        Field(description="Deduplication value for new ZFS folders."),
    ] = None
    nexenta_folder: Annotated[
        str | None,
        Field(description="A folder where cinder created datasets will reside."),
    ] = None
    nexenta_dataset_description: Annotated[
        str | None,
        Field(description="Human-readable description for the folder."),
    ] = None
    nexenta_blocksize: Annotated[
        int | None,
        Field(description="Block size for datasets"),
    ] = 4096
    nexenta_ns5_blocksize: Annotated[
        int | None,
        Field(description="Block size for datasets"),
    ] = 32
    nexenta_sparse: Annotated[
        bool | None,
        Field(description="Enables or disables the creation of sparse datasets"),
    ] = False
    nexenta_origin_snapshot_template: Annotated[
        str | None,
        Field(description="Template string to generate origin name of clone"),
    ] = "origin-snapshot-%s"
    nexenta_group_snapshot_template: Annotated[
        str | None,
        Field(description="Template string to generate group snapshot name"),
    ] = "group-snapshot-%s"
    nexenta_rrmgr_compression: Annotated[
        int | None,
        Field(
            description=(
                "Enable stream compression, level 1..9. 1 - gives best speed; "
                "9 - gives best compression."
            )
        ),
    ] = 0
    nexenta_rrmgr_tcp_buf_size: Annotated[
        int | None,
        Field(description="TCP Buffer size in KiloBytes."),
    ] = 4096
    nexenta_rrmgr_connections: Annotated[
        int | None,
        Field(description="Number of TCP connections."),
    ] = 2


class NexentaBackend(StorageBackendBase):
    """Nexenta iSCSI backend implementation."""

    backend_type = "nexenta"
    display_name = "Nexenta iSCSI"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-nexenta"

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
        return False

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration model type for this backend."""
        return NexentaConfig
