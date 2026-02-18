# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Dell PowerStore storage backend implementation using base step classes."""

import logging
from typing import Annotated, Literal, Optional

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class DellPowerstoreConfig(StorageBackendConfig):
    """Static configuration model for Dell PowerStore storage backend.

    This model includes all configuration options supported by the
    cinder-volume-dellpowerstore charm as defined in charmcraft.yaml.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str,
        Field(description="Dell PowerStore management IP"),
        SecretDictField(field="san-ip"),
    ]
    san_username: Annotated[
        str,
        Field(description="Dell PowerStore management username"),
        SecretDictField(field="san-username"),
    ]
    san_password: Annotated[
        str,
        Field(description=" Dell PowerStore management password"),
        SecretDictField(field="san-password"),
    ]
    protocol: Annotated[
        Optional[Literal["fc", "iscsi"]],
        Field(description="Storage protocol (fc or iscsi)"),
    ] = "fc"

    # Backend configuration
    volume_backend_name: Annotated[
        str | None, Field(description="Name that Cinder will report for this backend")
    ] = None
    backend_availability_zone: Annotated[
        str | None,
        Field(description="Availability zone to associate with this backend"),
    ] = None
    driver_ssl_cert: Annotated[
        str | None, Field(description="SSL certificate content in PEM format")
    ] = None

    # Optional
    powerstore_nvme: Annotated[
        bool | None,
        Field(description="Enables the connection to use NVMe based protocol"),
    ] = False
    powerstore_ports: Annotated[
        str | None,
        Field(description="Comma separated list of PowerStore iSCSI IPs or FC WWNs"),
    ] = None
    replication_device: Annotated[
        str | None, Field(description="Specific replication configuration settings")
    ] = None


class DellPowerstoreBackend(StorageBackendBase):
    """Dell PowerStore storage backend implementation."""

    backend_type = "dellpowerstore"
    display_name = "Dell PowerStore"

    @property
    def charm_name(self) -> str:
        """Return the charm name for this backend."""
        return "cinder-volume-dellpowerstore"

    @property
    def charm_channel(self) -> str:
        """Return the charm channel for this backend."""
        return "latest/edge"

    @property
    def charm_revision(self) -> str | None:
        """Return the charm revision for this backend."""
        return None

    @property
    def charm_base(self) -> str:
        """Return the charm base for this backend."""
        return "ubuntu@24.04"

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration class for Dell PowerStore backend."""
        return DellPowerstoreConfig
