# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""LINSTOR iSCSI backend implementation using base step classes."""

from typing import Annotated, Literal

from pydantic import Field

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase


class LinstorConfig(StorageBackendConfig):
    """Configuration model for LINSTOR iSCSI backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname.")
    ]

    # Optional backend configuration
    protocol: Annotated[
        Literal["iscsi"] | None,
        Field(description="Protocol selector: iscsi."),
    ] = None
    linstor_default_volume_group_name: Annotated[
        str | None,
        Field(
            description=("Default volume group name for LINSTOR (not Cinder volume).")
        ),
    ] = None
    linstor_default_uri: Annotated[
        str | None,
        Field(description="Default storage URI for LINSTOR."),
    ] = None
    linstor_default_storage_pool_name: Annotated[
        str | None,
        Field(description="Default Storage Pool name for LINSTOR."),
    ] = None
    linstor_volume_downsize_factor: Annotated[
        str | None,
        Field(description="Default volume downscale size in KiB = 4 MiB."),
    ] = None
    linstor_default_blocksize: Annotated[
        int | None,
        Field(
            description=(
                "Default block size for image restoration. "
                "When using iSCSI transport, this option specifies "
                "the block size."
            )
        ),
    ] = None
    linstor_autoplace_count: Annotated[
        int | None,
        Field(
            description=(
                "Autoplace replication count on volume deployment: "
                "0=full cluster without autoplace, 1=single-node "
                "without replication, >=2=replicated with autoplace."
            )
        ),
    ] = None
    linstor_controller_diskless: Annotated[
        bool | None,
        Field(description="True means Cinder node is a diskless LINSTOR node."),
    ] = None


class LinstorBackend(StorageBackendBase):
    """LINSTOR iSCSI backend implementation."""

    backend_type = "linstor"
    display_name = "LINSTOR iSCSI"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-linstor"

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
        return LinstorConfig
