# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""AS13000 backend implementation using base step classes."""

import logging
from typing import Annotated, Literal

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class Inspuras13000Config(StorageBackendConfig):
    """Configuration model for AS13000 backend.

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
    as13000_token_available_time: Annotated[
        str,
        Field(
            alias="as13000-token-available-time",
            description="The effective time of token validity in seconds.",
        ),
        SecretDictField(field="as13000-token-available-time"),
    ]

    # Optional backend configuration
    protocol: Annotated[
        Literal["iscsi"] | None,
        Field(description="Protocol selector: iscsi."),
    ] = None
    as13000_ipsan_pools: Annotated[
        str | None,
        Field(
            description="The Storage Pools Cinder should use, a comma separated list."
        ),
    ] = None
    as13000_meta_pool: Annotated[
        str | None,
        Field(
            description="The pool which is used as a meta pool when creating a volume."
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


class Inspuras13000Backend(StorageBackendBase):
    """AS13000 backend implementation."""

    backend_type = "inspuras13000"
    display_name = "AS13000"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-inspuras13000"

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
        return Inspuras13000Config
