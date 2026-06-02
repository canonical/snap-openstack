# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Dell Unity backend implementation using base step classes."""

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


class Protocol(StrEnum):
    """Enumeration of valid protocol types."""

    ISCSI = "iscsi"
    FC = "fc"


class DellunityConfig(StorageBackendConfig):
    """Configuration model for Dell Unity backend.

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
        Literal["iscsi", "fc"] | None,
        Field(description="Protocol selector: iscsi, fc."),
    ] = None
    unity_storage_pool_names: Annotated[
        str | None,
        Field(description="A comma-separated list of storage pool names to be used."),
    ] = None
    unity_io_ports: Annotated[
        str | None,
        Field(description="A comma-separated list of iSCSI or FC ports to be used."),
    ] = None
    remove_empty_host: Annotated[
        bool | None,
        Field(
            description=(
                "To remove the host from Unity when the last LUN is detached from it."
            )
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


class DellunityBackend(StorageBackendBase):
    """Dell Unity backend implementation."""

    backend_type = "dellunity"
    display_name = "Dell Unity"
    generally_available = False

    @property
    def charm_name(self) -> str:
        """Return the charm name for Dell Unity."""
        return "cinder-volume-dellunity"

    @property
    def charm_channel(self) -> str:
        """Return the default charm channel."""
        return "latest/edge"

    @property
    def charm_revision(self) -> str | None:
        """Return the pinned charm revision, if any."""
        return None

    @property
    def charm_base(self) -> str:
        """Return the charm base OS."""
        return "ubuntu@24.04"

    @property
    def supports_ha(self) -> bool:
        """Return whether this backend supports high availability."""
        return False

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration model class for Dell Unity."""
        return DellunityConfig
