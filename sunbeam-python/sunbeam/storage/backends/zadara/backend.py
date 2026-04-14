# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""ZadaraVPSA iSCSI backend implementation using base step classes."""

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


class ZadaraConfig(StorageBackendConfig):
    """Configuration model for ZadaraVPSA iSCSI backend.

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
    zadara_access_key: Annotated[
        str,
        Field(description="VPSA access key"),
        SecretDictField(field="zadara-access-key"),
    ]

    # Optional backend configuration
    zadara_vpsa_host: Annotated[
        str | None, Field(description="VPSA - Management Host name or IP address")
    ] = None
    zadara_vpsa_port: Annotated[int | None, Field(description="VPSA - Port number")] = (
        None
    )
    zadara_vpsa_use_ssl: Annotated[
        bool | None, Field(description="VPSA - Use SSL connection")
    ] = None
    zadara_ssl_cert_verify: Annotated[
        bool | None,
        Field(
            description="If set to True the http client will validate the SSL certificate of the VPSA endpoint."
        ),
    ] = None
    zadara_vpsa_poolname: Annotated[
        str | None, Field(description="VPSA - Storage Pool assigned for volumes")
    ] = None
    zadara_vol_encrypt: Annotated[
        bool | None, Field(description="VPSA - Default encryption policy for volumes.")
    ] = None
    zadara_gen3_vol_dedupe: Annotated[
        bool | None, Field(description="VPSA - Enable deduplication for volumes.")
    ] = None
    zadara_gen3_vol_compress: Annotated[
        bool | None, Field(description="VPSA - Enable compression for volumes.")
    ] = None
    zadara_default_snap_policy: Annotated[
        bool | None, Field(description="VPSA - Attach snapshot policy for volumes.")
    ] = None
    zadara_use_iser: Annotated[
        bool | None, Field(description="VPSA - Use ISER instead of iSCSI")
    ] = None
    zadara_vol_name_template: Annotated[
        str | None, Field(description="VPSA - Default template for VPSA volume names")
    ] = None


class ZadaraBackend(StorageBackendBase):
    """ZadaraVPSA iSCSI backend implementation."""

    backend_type = "zadara"
    display_name = "ZadaraVPSA iSCSI"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-zadara"

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
        return ZadaraConfig
