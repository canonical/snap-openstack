# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""INFINIDAT InfiniBox backend implementation using base step classes."""

import logging
from enum import StrEnum
from typing import Annotated

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class Protocol(StrEnum):
    """Enumeration of valid storage protocol types."""

    ISCSI = "iscsi"
    FC = "fc"


class InfinidatConfig(StorageBackendConfig):
    """Configuration model for INFINIDAT InfiniBox backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[str, Field(description="IP address of SAN controller")]
    san_login: Annotated[
        str,
        Field(description="Username for SAN controller"),
        SecretDictField(field="san-login"),
    ]
    san_password: Annotated[
        str,
        Field(description="Password for SAN controller"),
        SecretDictField(field="san-password"),
    ]

    # Optional backend configuration
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: iscsi, fc."),
    ] = None
    infinidat_pool_name: Annotated[
        str | None,
        Field(description="Name of the pool from which volumes are allocated"),
    ] = None
    infinidat_storage_protocol: Annotated[
        Protocol | None,
        Field(
            description="Protocol for transferring data between host and storage back-end."  # noqa: E501
        ),
    ] = None
    infinidat_iscsi_netspaces: Annotated[
        str | None,
        Field(
            description="List of names of network spaces to use for iSCSI connectivity"
        ),
    ] = None
    infinidat_use_compression: Annotated[
        bool | None,
        Field(
            description="Specifies whether to enable (true) or disable (false) compression for all newly created volumes."  # noqa: E501
        ),
    ] = None
    san_thin_provision: Annotated[
        bool | None,
        Field(description="Use thin provisioning for SAN volumes?"),
    ] = None
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = None


class InfinidatBackend(StorageBackendBase):
    """INFINIDAT InfiniBox backend implementation."""

    backend_type = "infinidat"
    display_name = "INFINIDAT InfiniBox"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-infinidat"

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

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration model type for this backend."""
        return InfinidatConfig
