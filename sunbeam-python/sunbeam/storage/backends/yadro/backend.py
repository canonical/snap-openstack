# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tatlin FCVolume backend implementation using base step classes."""

import logging
from enum import StrEnum
from typing import Annotated

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()


class Protocol(StrEnum):
    """Enumeration of valid protocol types."""

    FC = "fc"
    ISCSI = "iscsi"


class YadroConfig(StorageBackendConfig):
    """Configuration model for Tatlin FCVolume backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters (required: true in spec)
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname.")
    ]

    # Optional backend configuration (required: false in spec)
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: fc, iscsi."),
    ] = None
    pool_name: Annotated[
        str | None,
        Field(description="storage pool name"),
    ] = None
    api_port: Annotated[
        int | None,
        Field(description="Port to use to access the Tatlin API"),
    ] = None
    export_ports: Annotated[
        str | None,
        Field(description="Ports to export Tatlin resource through"),
    ] = None
    host_group: Annotated[
        str | None,
        Field(description="Tatlin host group name"),
    ] = None
    max_resource_count: Annotated[
        int | None,
        Field(description="Max resource count allowed for Tatlin"),
    ] = None
    pool_max_resource_count: Annotated[
        int | None,
        Field(description="Max resource count allowed for single pool"),
    ] = None
    tat_api_retry_count: Annotated[
        int | None,
        Field(description="Number of retry on Tatlin API"),
    ] = None
    auth_method: Annotated[
        str | None,
        Field(description="Authentication method for iSCSI (CHAP)"),
    ] = None
    lba_format: Annotated[
        str | None,
        Field(description="LBA Format for new volume"),
    ] = None
    wait_retry_count: Annotated[
        int | None,
        Field(description="Number of checks for a lengthy operation to finish"),
    ] = None
    wait_interval: Annotated[
        int | None,
        Field(description="Wait number of seconds before re-checking"),
    ] = None


class YadroBackend(StorageBackendBase):
    """Tatlin FCVolume backend implementation."""

    backend_type = "yadro"
    display_name = "Tatlin FCVolume"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-yadro"

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
        return YadroConfig
