# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""NetApp SolidFire backend implementation using base step classes."""

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


class ProvisioningCalc(StrEnum):
    """Enumeration of valid provisioning calculation types."""

    MAX_PROVISIONED_SPACE = "maxProvisionedSpace"
    USED_SPACE = "usedSpace"


class SolidFireConfig(StorageBackendConfig):
    """Configuration model for NetApp SolidFire backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="IP address of SAN controller")
    ]
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
    sf_emulate_512: Annotated[
        bool | None,
        Field(description="Set 512 byte emulation on volume creation;"),
    ] = None
    sf_allow_tenant_qos: Annotated[
        bool | None,
        Field(description="Allow tenants to specify QOS on create"),
    ] = None
    sf_account_prefix: Annotated[
        str | None,
        Field(description="Create SolidFire accounts with this prefix."),
    ] = None
    sf_volume_prefix: Annotated[
        str | None,
        Field(description="Create SolidFire volumes with this prefix."),
    ] = None
    sf_svip: Annotated[
        str | None,
        Field(description="Overrides default cluster SVIP with the one specified."),
    ] = None
    sf_api_port: Annotated[
        int | None,
        Field(description="SolidFire API port."),
    ] = None
    sf_enable_vag: Annotated[
        bool | None,
        Field(description="Utilize volume access groups on a per-tenant basis."),
    ] = None
    sf_provisioning_calc: Annotated[
        ProvisioningCalc | None,
        Field(description="Change how SolidFire reports used space and provisioning calculations."),
    ] = None
    sf_cluster_pairing_timeout: Annotated[
        int | None,
        Field(description="Sets time in seconds to wait for clusters to complete pairing."),
    ] = None
    sf_volume_pairing_timeout: Annotated[
        int | None,
        Field(description="Sets time in seconds to wait for a migrating volume to complete pairing and sync."),
    ] = None
    sf_api_request_timeout: Annotated[
        int | None,
        Field(description="Sets time in seconds to wait for an api request to complete."),
    ] = None
    sf_volume_clone_timeout: Annotated[
        int | None,
        Field(description="Sets time in seconds to wait for a clone of a volume or snapshot to complete."),
    ] = None
    sf_volume_create_timeout: Annotated[
        int | None,
        Field(description="Sets time in seconds to wait for a create volume operation to complete."),
    ] = None
    san_thin_provision: Annotated[
        bool | None,
        Field(description="Use thin provisioning for SAN volumes?"),
    ] = None
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = None


class SolidFireBackend(StorageBackendBase):
    """NetApp SolidFire backend implementation."""

    backend_type = "solidfire"
    display_name = "NetApp SolidFire"
    generally_available = True

    @property
    def charm_name(self) -> str:
        return "cinder-volume-solidfire"

    @property
    def charm_channel(self) -> str:
        return "latest/edge"

    @property
    def charm_revision(self) -> str | None:
        return None

    @property
    def charm_base(self) -> str:
        return "ubuntu@24.04"

    @property
    def supports_ha(self) -> bool:
        return True

    def config_type(self) -> type[StorageBackendConfig]:
        return SolidFireConfig
