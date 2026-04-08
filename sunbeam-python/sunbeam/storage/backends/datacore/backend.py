# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Ceph RBD backend implementation using base step classes."""

import logging
from typing import Annotated

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()


class CephConfig(StorageBackendConfig):
    """Configuration model for Ceph RBD backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    ceph_osd_replication_count: Annotated[
        int,
        Field(
            description="Number of replicas Ceph must make of any object in the cinder RBD pool"  # noqa: E501
        ),
    ] = 3
    ceph_pool_weight: Annotated[
        int,
        Field(
            description="Relative weighting of the pool as a percentage of total Ceph cluster data"  # noqa: E501
        ),
    ] = 20
    rbd_pool_name: Annotated[
        str | None,
        Field(
            description="Optionally specify an existing RBD pool to map Cinder volumes to"  # noqa: E501
        ),
    ] = None
    rbd_flatten_volume_from_snapshot: Annotated[
        bool | None,
        Field(
            description="Flatten volumes created from snapshots to remove snapshot dependency"  # noqa: E501
        ),
    ] = False
    rbd_mirroring_mode: Annotated[
        str | None, Field(description="Mode to use for RBD mirroring (pool or image)")
    ] = "pool"
    restrict_ceph_pools: Annotated[
        bool | None,
        Field(
            description="Optionally restrict Ceph key permissions to access only required pools"  # noqa: E501
        ),
    ] = False
    pool_type: Annotated[
        str | None, Field(description="Ceph pool type - replicated or erasure-coded")
    ] = "replicated"
    ec_profile_name: Annotated[
        str | None,
        Field(description="Name for the EC profile to be created for the EC pools"),
    ] = None
    ec_rbd_metadata_pool: Annotated[
        str | None,
        Field(description="Name of the metadata pool for erasure-coded RBD volumes"),
    ] = None
    ec_profile_k: Annotated[
        int | None, Field(description="Number of data chunks in the EC profile (k)")
    ] = 1
    ec_profile_m: Annotated[
        int | None, Field(description="Number of coding chunks in the EC profile (m)")
    ] = 2
    ec_profile_plugin: Annotated[
        str | None,
        Field(description="EC plugin to use (jerasure, isa, lrc, shec, clay)"),
    ] = "jerasure"
    ec_profile_technique: Annotated[
        str | None, Field(description="EC technique to use (varies by plugin)")
    ] = None
    ec_profile_locality: Annotated[
        int | None, Field(description="LRC locality for EC profile")
    ] = None
    ec_profile_crush_locality: Annotated[
        str | None, Field(description="CRUSH bucket type for LRC locality")
    ] = None
    ec_profile_durability_estimator: Annotated[
        int | None, Field(description="SHEC durability estimator for EC profile")
    ] = None
    ec_profile_helper_chunks: Annotated[
        int | None, Field(description="Number of helper chunks for CLAY EC profile")
    ] = None
    ec_profile_scalar_mds: Annotated[
        str | None, Field(description="Scalar MDS sub-chunk scheme for CLAY EC profile")
    ] = None
    ec_profile_device_class: Annotated[
        str | None, Field(description="CRUSH device class to use for EC profile OSDs")
    ] = None
    bluestore_compression_algorithm: Annotated[
        str | None,
        Field(description="Bluestore compression algorithm (snappy, zlib, zstd, lz4)"),
    ] = None
    bluestore_compression_mode: Annotated[
        str | None,
        Field(
            description="Bluestore compression mode (none, passive, aggressive, force)"
        ),
    ] = None
    bluestore_compression_required_ratio: Annotated[
        float | None,
        Field(
            description="Minimum compression ratio required for compression to be applied"  # noqa: E501
        ),
    ] = None
    bluestore_compression_min_blob_size: Annotated[
        int | None, Field(description="Minimum blob size for compression")
    ] = None
    bluestore_compression_min_blob_size_hdd: Annotated[
        int | None,
        Field(description="Minimum blob size for compression on HDD devices"),
    ] = None
    bluestore_compression_min_blob_size_ssd: Annotated[
        int | None,
        Field(description="Minimum blob size for compression on SSD devices"),
    ] = None
    bluestore_compression_max_blob_size: Annotated[
        int | None, Field(description="Maximum blob size for compression")
    ] = None
    bluestore_compression_max_blob_size_hdd: Annotated[
        int | None,
        Field(description="Maximum blob size for compression on HDD devices"),
    ] = None
    bluestore_compression_max_blob_size_ssd: Annotated[
        int | None,
        Field(description="Maximum blob size for compression on SSD devices"),
    ] = None
    image_volume_cache_enabled: Annotated[
        bool | None, Field(description="Enable the Cinder image volume cache")
    ] = False
    image_volume_cache_max_size_gb: Annotated[
        int | None,
        Field(
            description="Max size of the image volume cache in GB. 0 means unlimited"
        ),
    ] = 0
    image_volume_cache_max_count: Annotated[
        int | None,
        Field(
            description="Max number of entries allowed in the image volume cache. 0 means unlimited"  # noqa: E501
        ),
    ] = 0


class CephBackend(StorageBackendBase):
    """Ceph RBD backend implementation."""

    backend_type = "ceph"
    display_name = "Ceph RBD"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-ceph"

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
        return CephConfig
