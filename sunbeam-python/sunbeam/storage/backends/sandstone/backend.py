# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Sds iSCSI backend implementation using base step classes."""

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

    ISCSI = "iscsi"


class SandstoneConfig(StorageBackendConfig):
    """Configuration model for Sds iSCSI backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname.")
    ]
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: iscsi."),
    ] = None

    # Optional backend configuration
    default_sandstone_target_ips: Annotated[
        str | None,
        Field(description="SandStone default target ip."),
    ] = None
    sandstone_pool: Annotated[
        str | None,
        Field(description="SandStone storage pool resource name."),
    ] = None
    initiator_assign_sandstone_target_ip: Annotated[
        str | None,
        Field(description="Support initiator assign target with assign ip."),
    ] = None


class SandstoneBackend(StorageBackendBase):
    """Sds iSCSI backend implementation."""

    backend_type = "sandstone"
    display_name = "Sds iSCSI"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-sandstone"

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
        return SandstoneConfig
