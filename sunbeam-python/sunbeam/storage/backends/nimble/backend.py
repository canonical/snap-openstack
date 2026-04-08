# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""HPE Nimble Storage backend implementation using base step classes."""

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


class NimbleConfig(StorageBackendConfig):
    """Configuration model for HPE Nimble Storage backend.

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
    nimble_pool_name: Annotated[
        str | None,
        Field(description="Nimble Controller pool name"),
    ] = None
    nimble_subnet_label: Annotated[
        str | None,
        Field(description="Nimble Subnet Label"),
    ] = None
    nimble_verify_certificate: Annotated[
        bool | None,
        Field(description="Whether to verify Nimble SSL Certificate"),
    ] = None
    nimble_verify_cert_path: Annotated[
        str | None,
        Field(description="Path to Nimble Array SSL certificate"),
    ] = None
    san_thin_provision: Annotated[
        bool | None,
        Field(description="Use thin provisioning for SAN volumes?"),
    ] = None
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = None


class NimbleBackend(StorageBackendBase):
    """HPE Nimble Storage backend implementation."""

    backend_type = "nimble"
    display_name = "HPE Nimble Storage"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-nimble"

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
        return NimbleConfig
