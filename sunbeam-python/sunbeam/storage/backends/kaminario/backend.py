# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Kaminario iSCSI backend implementation using base step classes."""

import logging
from typing import Annotated, Literal

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()


class KaminarioConfig(StorageBackendConfig):
    """Configuration model for Kaminario iSCSI backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname")
    ]

    # Optional backend configuration
    protocol: Annotated[
        Literal["iscsi"] | None,
        Field(description="Protocol selector: iscsi."),
    ] = None
    auto_calc_max_oversubscription_ratio: Annotated[
        bool | None,
        Field(
            description="K2 driver will calculate max_oversubscription_ratio on setting this option as True"  # noqa: E501
        ),
    ] = None
    disable_discovery: Annotated[
        bool | None,
        Field(
            description="Disabling iSCSI discovery (sendtargets) for multipath connections on K2 driver"  # noqa: E501
        ),
    ] = None


class KaminarioBackend(StorageBackendBase):
    """Kaminario iSCSI backend implementation."""

    backend_type = "kaminario"
    display_name = "Kaminario iSCSI"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-kaminario"

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
        return KaminarioConfig
