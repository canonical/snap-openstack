# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storwize SVC backend implementation using base step classes."""

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


class IbmStorwizeSVCConfig(StorageBackendConfig):
    """Configuration model for Storwize SVC backend.

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
    storwize_svc_volpool_name: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated list of storage system storage pools for volumes."
            )
        ),
    ] = None
    storwize_svc_vol_rsize: Annotated[
        int | None,
        Field(
            description=(
                "Storage system space-efficiency parameter for volumes (percentage)"
            )
        ),
    ] = None
    storwize_svc_vol_warning: Annotated[
        int | None,
        Field(
            description=(
                "Storage system threshold for volume capacity warnings (percentage)"
            )
        ),
    ] = None
    storwize_svc_vol_autoexpand: Annotated[
        bool | None,
        Field(
            description="Storage system autoexpand parameter for volumes (True/False)"
        ),
    ] = None
    storwize_svc_vol_grainsize: Annotated[
        int | None,
        Field(
            description=(
                "Storage system grain size parameter for volumes (8/32/64/128/256)"
            )
        ),
    ] = None
    storwize_svc_vol_compression: Annotated[
        bool | None,
        Field(description="Storage system compression option for volumes"),
    ] = None
    storwize_svc_vol_easytier: Annotated[
        bool | None,
        Field(description="Enable Easy Tier for volumes"),
    ] = None
    storwize_svc_vol_iogrp: Annotated[
        str | None,
        Field(
            description=(
                "The I/O group in which to allocate volumes. "
                "It can be a comma-separated list."
            )
        ),
    ] = None
    storwize_svc_flashcopy_timeout: Annotated[
        int | None,
        Field(
            description=(
                "Maximum number of seconds to wait for FlashCopy to be prepared."
            )
        ),
    ] = None
    storwize_svc_allow_tenant_qos: Annotated[
        bool | None,
        Field(description="Allow tenants to specify QOS on create"),
    ] = None
    storwize_svc_stretched_cluster_partner: Annotated[
        str | None,
        Field(
            description=(
                "If operating in stretched cluster mode, specify "
                "the name of the pool in which mirrored copies are stored."
            )
        ),
    ] = None
    storwize_san_secondary_ip: Annotated[
        str | None,
        Field(
            description=(
                "Specifies secondary management IP or hostname "
                "to be used if san_ip is invalid or inaccessible."
            )
        ),
    ] = None
    storwize_svc_vol_nofmtdisk: Annotated[
        bool | None,
        Field(
            description="Specifies that the volume not be formatted during creation."
        ),
    ] = None
    storwize_svc_flashcopy_rate: Annotated[
        int | None,
        Field(
            description=(
                "Specifies the Storwize FlashCopy copy rate to "
                "be used when creating a full volume copy."
            )
        ),
    ] = None
    storwize_svc_clean_rate: Annotated[
        int | None,
        Field(description="Specifies the Storwize cleaning rate for the mapping."),
    ] = None
    storwize_svc_mirror_pool: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the name of the pool in which mirrored copy is stored."
            )
        ),
    ] = None
    storwize_svc_aux_mirror_pool: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the name of the pool in which mirrored "
                "copy is stored for aux volume."
            )
        ),
    ] = None
    storwize_portset: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the name of the portset in which the host is to be created."
            )
        ),
    ] = None
    storwize_svc_src_child_pool: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the source child pool for global "
                "mirror source change volume storage."
            )
        ),
    ] = None
    storwize_svc_target_child_pool: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the target child pool for global mirror "
                "auxiliary change volume storage."
            )
        ),
    ] = None
    storwize_peer_pool: Annotated[
        str | None,
        Field(
            description=(
                "Specifies the peer pool for hyperswap volume; "
                "the peer pool must exist on the other site."
            )
        ),
    ] = None
    storwize_preferred_host_site: Annotated[
        str | None,
        Field(
            description=(
                "Specifies site information for host. One WWPN "
                "or multiple WWPNs used in the host can be specified."
            )
        ),
    ] = None
    cycle_period_seconds: Annotated[
        int | None,
        Field(
            description=(
                "Defines an optional cycle period that applies to "
                "Global Mirror relationships with cycling mode multi."
            )
        ),
    ] = None
    storwize_svc_retain_aux_volume: Annotated[
        bool | None,
        Field(
            description=(
                "Enable or disable retaining aux volume on "
                "secondary storage when deleting the primary volume."
            )
        ),
    ] = None
    migrate_from_flashcopy: Annotated[
        bool | None,
        Field(
            description=(
                "Allow or prevent volumes with legacy FlashCopy "
                "mappings from joining volume group features."
            )
        ),
    ] = None
    storwize_svc_multipath_enabled: Annotated[
        bool | None,
        Field(
            description=(
                "Connect with multipath (FC only; iSCSI "
                "multipath is controlled by Nova)."
            )
        ),
    ] = None
    storwize_svc_iscsi_chap_enabled: Annotated[
        bool | None,
        Field(
            description=(
                "Configure CHAP authentication for iSCSI "
                "connections (default: enabled)."
            )
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


class IbmStorwizeSVCBackend(StorageBackendBase):
    """Storwize SVC backend implementation."""

    backend_type = "ibmstorwizesvc"
    display_name = "IBM Storwize SVC"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-ibmstorwizesvc"

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
        return IbmStorwizeSVCConfig
