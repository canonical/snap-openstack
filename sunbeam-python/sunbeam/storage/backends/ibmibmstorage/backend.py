# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""IBMStorage backend implementation using base step classes."""

import logging
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class ConnectionType(StrEnum):
    """Enumeration of valid connection types."""

    FIBRE_CHANNEL = "fibre_channel"
    ISCSI = "iscsi"


class Chap(StrEnum):
    """Enumeration of CHAP authentication modes."""

    DISABLED = "disabled"
    ENABLED = "enabled"


class IbmibmstorageConfig(StorageBackendConfig):
    """Configuration model for IBMStorage backend.

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
        Literal["fc", "iscsi"] | None,
        Field(description="Protocol selector: fc, iscsi."),
    ] = None
    ds8k_devadd_unitadd_mapping: Annotated[
        str | None,
        Field(description="Mapping between IODevice address and unit address."),
    ] = None
    ds8k_ssid_prefix: Annotated[
        str | None,
        Field(description="Set the first two digits of SSID."),
    ] = "FF"
    lss_range_for_cg: Annotated[
        str | None,
        Field(description="Reserve LSSs for consistency group."),
    ] = None
    ds8k_host_type: Annotated[
        str | None,
        Field(
            description=(
                'DS8K host type identifier. Use "auto" for automatic host '
                "type selection, or provide a value supported by the array."
            )
        ),
    ] = "auto"
    proxy: Annotated[
        str | None,
        Field(description="Proxy driver that connects to the IBM Storage Array"),
    ] = "cinder.volume.drivers.ibm.ibm_storage.proxy.IBMStorageProxy"
    connection_type: Annotated[
        ConnectionType | None,
        Field(description="Connection type to the IBM Storage Array"),
    ] = None
    chap: Annotated[
        Chap | None,
        Field(
            description="CHAP authentication mode, effective only for iscsi (disabled|enabled)"  # noqa: E501
        ),
    ] = None
    management_ips: Annotated[
        str | None,
        Field(description="List of Management IP addresses (separated by commas)"),
    ] = None
    san_thin_provision: Annotated[
        bool | None,
        Field(description="Use thin provisioning for SAN volumes?"),
    ] = True
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = True

    @model_validator(mode="after")
    def validate_protocol_connection_consistency(self):
        """Ensure protocol and connection_type do not conflict when both set."""
        if self.protocol is None or self.connection_type is None:
            return self

        expected = (
            ConnectionType.FIBRE_CHANNEL
            if self.protocol == "fc"
            else ConnectionType.ISCSI
        )
        if self.connection_type != expected:
            raise ValueError(
                "protocol and connection_type must be consistent "
                "(fc<->fibre_channel, iscsi<->iscsi)"
            )
        return self


class IbmibmstorageBackend(StorageBackendBase):
    """IBMStorage backend implementation."""

    backend_type = "ibmibmstorage"
    display_name = "IBMStorage"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-ibmibmstorage"

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
        return IbmibmstorageConfig
