# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""FJDX FC backend implementation using base step classes."""

import logging
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()


class Protocol(StrEnum):
    """Enumeration of valid protocol types."""

    FC = "fc"


class FujitsueternusdxConfig(StorageBackendConfig):
    """Configuration model for FJDX FC backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname")
    ]
    fujitsu_passwordless: Annotated[
        bool,
        Field(description="Whether to use SSH key authentication to connect."),
    ] = False

    # Optional backend configuration
    protocol: Annotated[
        Literal["fc"] | None,
        Field(description="Protocol selector: fc."),
    ] = None
    cinder_eternus_config_file: Annotated[
        str | None,
        Field(description="Config file for cinder eternus_dx volume driver."),
    ] = None
    fujitsu_private_key_path: Annotated[
        str | None,
        Field(
            description="Filename of private key for ETERNUS CLI when SSH key authentication is enabled."  # noqa: E501
        ),
    ] = None
    fujitsu_use_cli_copy: Annotated[
        bool | None,
        Field(description="If True use CLI command to create snapshot."),
    ] = None


class FujitsueternusdxBackend(StorageBackendBase):
    """FJDX FC backend implementation."""

    backend_type = "fujitsueternusdx"
    display_name = "FJDX FC"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-fujitsueternusdx"

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
        return FujitsueternusdxConfig
