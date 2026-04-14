# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Jovian iSCSI backend implementation using base step classes."""

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


class BlockSize(StrEnum):
    """Enumeration of valid block sizes."""

    K16 = "16K"
    K32 = "32K"
    K64 = "64K"
    K128 = "128K"
    K256 = "256K"
    K512 = "512K"
    M1 = "1M"


class OpeneConfig(StorageBackendConfig):
    """Configuration model for Jovian iSCSI backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname.")
    ]
    protocol: Annotated[
        Literal["iscsi"] | None,
        Field(description="Protocol selector: iscsi."),
    ] = None
    san_hosts: Annotated[
        str | None, Field(description="IP address of Open-E JovianDSS SA")
    ] = None
    jovian_recovery_delay: Annotated[
        int | None, Field(description="Time before HA cluster failure.")
    ] = None
    jovian_ignore_tpath: Annotated[
        str | None, Field(description="List of multipath ip addresses to ignore.")
    ] = None
    chap_password_len: Annotated[
        str,
        Field(description="Length of the random string for CHAP password."),
        SecretDictField(field="chap-password-len"),
    ]
    jovian_pool: Annotated[
        str | None, Field(description="JovianDSS pool that holds all cinder volumes")
    ] = None
    jovian_block_size: Annotated[
        BlockSize | None, Field(description="Block size for new volume")
    ] = None


class OpeneBackend(StorageBackendBase):
    """Jovian iSCSI backend implementation."""

    backend_type = "opene"
    display_name = "Jovian iSCSI"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-opene"

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
        return OpeneConfig
