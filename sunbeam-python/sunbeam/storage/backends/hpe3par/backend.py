# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""HPE 3Par backend implementation using base step classes."""

import logging
from typing import Annotated, Literal, Optional

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class HPEthreeparConfig(StorageBackendConfig):
    """Configuration model for HPE 3Par backend.

    This model includes the configuration options for deploying
    a HPE 3Par backend.
    """

    # Mandatory connection parameters
    san_ip: Annotated[str, Field(description="HPE 3Par management IP or hostname")]
    san_login: Annotated[
        str,
        Field(description="HPE 3Par management login username"),
        SecretDictField(field="san-login"),
    ]
    san_password: Annotated[
        str,
        Field(description="HPE 3Par management login password"),
        SecretDictField(field="san-password"),
    ]
    hpe3par_api_url: Annotated[
        str | None,
        Field(description="HPE 3Par WSAPI url"),
    ] = None
    hpe3par_username: Annotated[
        str | None,
        Field(description="HPE 3Par user with admin role"),
        SecretDictField(field="hpe3par-username"),
    ] = None
    hpe3par_password: Annotated[
        str | None,
        Field(
            description="HPE 3Par password for the specified user",
        ),
        SecretDictField(field="hpe3par-password"),
    ] = None

    # Replication settings
    replication_device: Annotated[
        str | None,
        Field(
            description="""Specific replication configuration settings.
            Must be set under the form of
            backend_id:hpe3par_device_2,
            san_ip: <Replication system San ip>,
            san_login: <Replication system San username>,
            san_password: <Replication system San password>"
            """
        ),
    ] = None

    # Optional backend configuration
    protocol: Annotated[
        Optional[Literal["iscsi", "fc"]],
        Field(description="HPE 3Par protocol (iscsi, fc)"),
    ] = "fc"
    hpe3par_cpg: Annotated[
        str | None,
        Field(
            description="HPE 3Par list of CPG(s) to use for volume creation",
        ),
    ] = None
    hpe3par_target_nsp: Annotated[
        str | None,
        Field(
            description="HPE 3Par The nsp of the backend to be used",
        ),
    ] = None
    hpe3par_debug: Annotated[
        bool | None,
        Field(description="HPE 3Par enable debug for WSAPI calls"),
    ] = False
    hpe3par_snapshot_retention: Annotated[
        str | None,
        Field(
            description="HPE 3Par snapshot retention time in hours",
        ),
    ] = None
    hpe3par_snapshot_expiration: Annotated[
        str | None,
        Field(
            description="HPE 3Par snapshot expiration time in hours",
        ),
    ] = None
    hpe3par_cpg_snap: Annotated[
        str | None,
        Field(
            description="HPE 3Par list of CPG(s) to use for snapshot volume creation",
        ),
    ] = None

    # Protocol_specific options
    hpe3par_iscsi_ips: Annotated[
        str | None,
        Field(
            description="HPE 3Par list of iSCSI addresses:[port] to use",
        ),
    ] = None
    hpe3par_iscsi_chap_enabled: Annotated[
        bool,
        Field(
            description="HPE 3Par enable CHAP authentication for iSCSI connections",
        ),
    ] = False

    # Performance options
    reserved_percentage: Annotated[
        int | None,
        Field(
            description="""
            This flag represents the percentage of reserved back_end capacity
            """
        ),
    ] = None
    max_over_subscription_ratio: Annotated[
        float | None,
        Field(
            description="""
            The ratio of oversubscription when thin provisioned volumes are involved."
            """
        ),
    ] = None
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(
            description="Enable multipathing for image transfer operations",
        ),
    ] = False
    enforce_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(
            description="Enforce multipathing for image transfer operations",
        ),
    ] = False


class HPEthreeparBackend(StorageBackendBase):
    """HPE 3Par backend implementation."""

    backend_type = "hpe3par"
    display_name = "HPE 3Par"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm name for this backend."""
        return "cinder-volume-hpe3par"

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

    @property
    def supports_ha(self) -> bool:
        """Return whether this backend supports HA deployments."""
        return True

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration class for HPE 3Par backend."""
        return HPEthreeparConfig
