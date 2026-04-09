# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""FlashSystem iSCSI backend implementation using base step classes."""

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


class IbmflashsystemiscsiConfig(StorageBackendConfig):
    """Configuration model for FlashSystem iSCSI backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname.")
    ]

    # Optional backend configuration
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: iscsi."),
    ] = None
    flashsystem_iscsi_portid: Annotated[
        int | None,
        Field(description="Default iSCSI Port ID of FlashSystem. (Default port is 0.)"),  # noqa: E501
    ] = None


class IbmflashsystemiscsiBackend(StorageBackendBase):
    """FlashSystem iSCSI backend implementation."""

    backend_type = "ibmflashsystemiscsi"
    display_name = "FlashSystem iSCSI"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-ibmflashsystemiscsi"

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
        return IbmflashsystemiscsiConfig
