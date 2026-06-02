# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Huawei OceanStor Dorado backend implementation using base step classes."""

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
    FC = "fc"


class HuaweiConfig(StorageBackendConfig):
    """Configuration model for Huawei OceanStor Dorado backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str,
        Field(
            description="IP address or hostname of the Huawei OceanStor storage array"
        ),
    ]
    san_login: Annotated[
        str,
        Field(description="Username for Huawei storage array REST API"),
        SecretDictField(field="san-login"),
    ]
    san_password: Annotated[
        str,
        Field(description="Password for Huawei storage array REST API"),
        SecretDictField(field="san-password"),
    ]

    # Optional backend configuration
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: iscsi, fc."),
    ] = None

    cinder_huawei_conf_file: Annotated[
        str | None,
        Field(description="The configuration file for the Cinder Huawei driver."),
    ] = None

    hypermetro_devices: Annotated[
        str | None,
        Field(description="The remote device hypermetro will use."),
    ] = None

    metro_san_user: Annotated[
        str | None,
        Field(description="The remote metro device san user."),
    ] = None

    metro_san_password: Annotated[
        str | None,
        Field(
            description=(
                "The remote metro device san password"
                " (only needed for HyperMetro replication)."
            )
        ),
        SecretDictField(field="metro-san-password"),
    ] = None

    metro_domain_name: Annotated[
        str | None,
        Field(description="The remote metro device domain name."),
    ] = None

    metro_san_address: Annotated[
        str | None,
        Field(description="The remote metro device request url."),
    ] = None

    metro_storage_pools: Annotated[
        str | None,
        Field(description="The remote metro device pool names."),
    ] = None


class HuaweiBackend(StorageBackendBase):
    """Huawei OceanStor Dorado backend implementation."""

    backend_type = "huawei"
    display_name = "Huawei OceanStor Dorado"
    generally_available = False

    @property
    def charm_name(self) -> str:
        """Return the charm name for Huawei OceanStor Dorado."""
        return "cinder-volume-huawei"

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
        """Return the configuration model class for Huawei OceanStor Dorado."""
        return HuaweiConfig
