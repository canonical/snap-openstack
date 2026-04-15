# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Stx backend implementation using base step classes."""

import logging
from enum import StrEnum
from typing import Annotated

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()


class PoolType(StrEnum):
    """Enumeration of valid pool types."""

    LINEAR = "linear"
    VIRTUAL = "virtual"


class Protocol(StrEnum):
    """Enumeration of valid protocol types."""

    ISCSI = "iscsi"


class StxConfig(StorageBackendConfig):
    """Configuration model for Stx backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname")
    ]
    # Optional connection configuration
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: iscsi."),
    ] = None

    # Optional backend configuration
    seagate_pool_name: Annotated[
        str | None,
        Field(description="Pool or vdisk name to use for volume creation"),
    ] = "A"
    seagate_pool_type: Annotated[
        PoolType | None,
        Field(description="linear (for vdisk) or virtual (for virtual pool)"),
    ] = None
    seagate_iscsi_ips: Annotated[
        str | None,
        Field(description="List of comma-separated target iSCSI IP addresses"),
    ] = None


class StxBackend(StorageBackendBase):
    """Stx backend implementation."""

    backend_type = "stx"
    display_name = "Stx"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-stx"

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
        return StxConfig
