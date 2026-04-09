# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""MacroSAN backend implementation using base step classes."""

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


class MacrosanConfig(StorageBackendConfig):
    """Configuration model for MacroSAN backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname.")
    ]

    macrosan_sdas_password: Annotated[
        str,
        Field(description="MacroSAN sdas devices' password"),
        SecretDictField(field="macrosan-sdas-password"),
    ]

    macrosan_replication_password: Annotated[
        str,
        Field(description="MacroSAN replication devices' password"),
        SecretDictField(field="macrosan-replication-password"),
    ]

    # Optional backend configuration
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: iscsi, fc."),
    ] = None

    macrosan_sdas_ipaddrs: Annotated[
        str | None,
        Field(description="MacroSAN sdas devices' ip addresses"),
    ] = None

    macrosan_sdas_username: Annotated[
        str | None,
        Field(description="MacroSAN sdas devices' username"),
    ] = None

    macrosan_replication_ipaddrs: Annotated[
        str | None,
        Field(description="MacroSAN replication devices' ip addresses"),
    ] = None

    macrosan_replication_username: Annotated[
        str | None,
        Field(description="MacroSAN replication devices' username"),
    ] = None

    macrosan_replication_destination_ports: Annotated[
        str | None,
        Field(description="Slave device"),
    ] = None

    macrosan_pool: Annotated[
        str | None,
        Field(description="Pool to use for volume creation"),
    ] = None

    macrosan_thin_lun_extent_size: Annotated[
        int | None,
        Field(description="Set the thin lun's extent size"),
    ] = None

    macrosan_thin_lun_low_watermark: Annotated[
        int | None,
        Field(description="Set the thin lun's low watermark"),
    ] = None

    macrosan_thin_lun_high_watermark: Annotated[
        int | None,
        Field(description="Set the thin lun's high watermark"),
    ] = None

    macrosan_force_unmap_itl: Annotated[
        bool | None,
        Field(description="Force disconnect while deleting volume"),
    ] = None

    macrosan_snapshot_resource_ratio: Annotated[
        str | None,
        Field(description="Set snapshot's resource ratio"),
    ] = None

    macrosan_log_timing: Annotated[
        bool | None,
        Field(description="Whether enable log timing"),
    ] = None

    macrosan_fc_use_sp_port_nr: Annotated[
        int | None,
        Field(
            description=(
                "The use_sp_port_nr parameter is the number of online FC ports "
                "used when FC connection is established in switch non-all-pass "
                "mode. The maximum is 4."
            )
        ),
    ] = None

    macrosan_fc_keep_mapped_ports: Annotated[
        bool | None,
        Field(
            description=(
                "In FC connections, keep the configuration item "
                "associated with the port."
            )
        ),
    ] = None

    macrosan_client: Annotated[
        str | None,
        Field(
            description=(
                "MacroSAN iSCSI clients list. Configure one or more entries in "
                "format: (host;client_name;sp1_iscsi_port;sp2_iscsi_port). "
                "client_name supports [a-zA-Z0-9.-_:] up to 31 chars."
            )
        ),
    ] = None

    macrosan_client_default: Annotated[
        str | None,
        Field(
            description=(
                "Default iSCSI connection port names used when "
                "no host-specific information is available, e.g. "
                "eth-1:0/eth-1:1;eth-2:0/eth-2:1."
            )
        ),
    ] = None


class MacrosanBackend(StorageBackendBase):
    """MacroSAN backend implementation."""

    backend_type = "macrosan"
    display_name = "MacroSAN"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-macrosan"

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
        return MacrosanConfig
