# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""QNAP Storage backend implementation using base step classes."""

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
    """Enumeration of valid protocol types."""

    FC = "fc"
    ISCSI = "iscsi"


class QnapConfig(StorageBackendConfig):
    """Configuration model for QNAP Storage backend.

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
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: fc, iscsi."),
    ] = None

    # Optional backend configuration
    qnap_management_url: Annotated[
        str | None,
        Field(
            description=(
                "URL to manage the QNAP storage system. The driver does not "
                "support IPv6 addresses in the URL."
            )
        ),
    ] = None
    qnap_poolname: Annotated[
        str | None,
        Field(description="The pool name in the QNAP Storage"),
    ] = None
    qnap_storage_protocol: Annotated[
        str | None,
        Field(description="Communication protocol to access QNAP storage"),
    ] = None
    san_thin_provision: Annotated[
        bool | None,
        Field(description="Use thin provisioning for SAN volumes?"),
    ] = None
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = None


class QnapBackend(StorageBackendBase):
    """QNAP Storage backend implementation."""

    backend_type = "qnap"
    display_name = "QNAP Storage"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-qnap"

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
        return QnapConfig
