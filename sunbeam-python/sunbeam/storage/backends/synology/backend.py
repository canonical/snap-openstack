# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Syno iSCSI backend implementation using base step classes."""

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

    ISCSI = "iscsi"


class SynologyConfig(StorageBackendConfig):
    """Configuration model for Syno iSCSI backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname.")
    ]
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: iscsi."),
    ] = None

    synology_password: Annotated[
        str,
        Field(description="Password of administrator for logging in Synology storage."),
        SecretDictField(field="synology-password"),
    ]

    synology_one_time_pass: Annotated[
        str,
        Field(
            description="One time password of administrator for logging in Synology storage if OTP is enabled."
        ),
        SecretDictField(field="synology-one-time-pass"),
    ]

    # Optional backend configuration
    synology_pool_name: Annotated[
        str | None,
        Field(description="Volume on Synology storage to be used for creating lun."),
    ] = None

    synology_admin_port: Annotated[
        int | None,
        Field(description="Management port for Synology storage."),
    ] = 5000

    synology_username: Annotated[
        str | None,
        Field(description="Administrator of Synology storage."),
    ] = "admin"

    synology_ssl_verify: Annotated[
        bool | None,
        Field(
            description="Do certificate validation or not if $driver_use_ssl is True"
        ),
    ] = True

    synology_device_id: Annotated[
        str | None,
        Field(
            description="Device id for skip one time password check for logging in Synology storage if OTP is enabled."
        ),
    ] = None


class SynologyBackend(StorageBackendBase):
    """Syno iSCSI backend implementation."""

    backend_type = "synology"
    display_name = "Syno iSCSI"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-synology"

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
        return SynologyConfig
