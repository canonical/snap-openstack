# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Inspur InStorageMCS backend implementation using base step classes."""

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


class InspurinstorageConfig(StorageBackendConfig):
    """Configuration model for Inspur InStorageMCS backend.

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
        Field(description="Protocol selector: fc, iscsi."),
    ] = None

    instorage_mcs_vol_autoexpand: Annotated[
        bool | None,
        Field(
            description=("Storage system autoexpand parameter for volumes (True/False)")
        ),
    ] = None
    instorage_mcs_vol_compression: Annotated[
        bool | None, Field(description="Storage system compression option for volumes")
    ] = None
    instorage_mcs_vol_intier: Annotated[
        bool | None, Field(description="Enable InTier for volumes")
    ] = None
    instorage_mcs_allow_tenant_qos: Annotated[
        bool | None, Field(description="Allow tenants to specify QOS on create")
    ] = None
    instorage_mcs_vol_grainsize: Annotated[
        int | None,
        Field(
            description=(
                "Storage system grain size parameter for volumes (32/64/128/256)"
            )
        ),
    ] = None
    instorage_mcs_vol_rsize: Annotated[
        int | None,
        Field(
            description=(
                "Storage system space-efficiency parameter for volumes (percentage)"
            )
        ),
    ] = None
    instorage_mcs_vol_warning: Annotated[
        int | None,
        Field(
            description=(
                "Storage system threshold for volume capacity warnings (percentage)"
            )
        ),
    ] = None
    instorage_mcs_localcopy_timeout: Annotated[
        int | None,
        Field(
            description=(
                "Maximum number of seconds to wait for LocalCopy to be prepared."
            )
        ),
    ] = None
    instorage_mcs_localcopy_rate: Annotated[
        int | None,
        Field(
            description=(
                "Specifies the InStorage LocalCopy copy rate "
                "used when creating a full volume copy."
            )
        ),
    ] = None
    instorage_mcs_vol_iogrp: Annotated[
        str | None, Field(description="The I/O group in which to allocate volumes.")
    ] = None
    instorage_san_secondary_ip: Annotated[
        str | None,
        Field(
            description=(
                "Specifies secondary management IP or hostname "
                "used if san_ip is invalid or inaccessible."
            )
        ),
    ] = None
    instorage_mcs_volpool_name: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated list of storage system storage pools for volumes."
            )
        ),
    ] = None
    instorage_mcs_iscsi_chap_enabled: Annotated[
        bool | None,
        Field(
            description=(
                "Configure CHAP authentication for iSCSI "
                "connections (default: enabled)."
            )
        ),
    ] = None
    san_thin_provision: Annotated[
        bool | None, Field(description="Use thin provisioning for SAN volumes?")
    ] = None
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = None


class InspurinstorageBackend(StorageBackendBase):
    """Inspur InStorageMCS backend implementation."""

    backend_type = "inspurinstorage"
    display_name = "InStorageMCS"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-inspurinstorage"

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
        return InspurinstorageConfig
