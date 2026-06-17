# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""DataCore backend implementation using base step classes."""

import logging
from typing import Annotated, Literal

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class DatacoreConfig(StorageBackendConfig):
    """Configuration model for DataCore SANsymphony backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str,
        Field(description="IP address or hostname of the DataCore management endpoint"),
    ]
    san_login: Annotated[
        str,
        Field(description="Username for DataCore management authentication"),
        SecretDictField(field="san-login"),
    ]
    san_password: Annotated[
        str,
        Field(description="Password for DataCore management authentication"),
        SecretDictField(field="san-password"),
    ]

    # Protocol selection
    protocol: Annotated[
        Literal["iscsi", "fc"] | None,
        Field(description="Front-end protocol used by DataCore (iscsi or fc)"),
    ] = None

    # DataCore-specific options
    datacore_disk_pools: Annotated[
        str | None,
        Field(
            description="Comma-separated list of DataCore disk pools to use for virtual disk creation"  # noqa: E501
        ),
    ] = None
    datacore_disk_type: Annotated[
        Literal["single", "mirrored"] | None,
        Field(
            description="Virtual disk type: single or mirrored (mirrored requires two DataCore servers)"  # noqa: E501
        ),
    ] = None
    datacore_storage_profile: Annotated[
        str | None,
        Field(
            description="Storage profile for virtual disk (Critical, High, Normal, Low, Archive)"  # noqa: E501
        ),
    ] = None
    datacore_api_timeout: Annotated[
        int | None,
        Field(description="Timeout in seconds for DataCore API calls"),
    ] = None
    datacore_disk_failed_delay: Annotated[
        int | None,
        Field(
            description="Timeout in seconds to wait for a virtual disk to leave Failed state"  # noqa: E501
        ),
    ] = None

    # iSCSI-specific options
    datacore_iscsi_unallowed_targets: Annotated[
        str | None,
        Field(
            description="Comma-separated list of iSCSI targets that cannot be used for volume attachment"  # noqa: E501
        ),
    ] = None
    use_chap_auth: Annotated[
        bool | None,
        Field(description="Enable CHAP authentication for iSCSI targets"),
    ] = None


class DatacoreBackend(StorageBackendBase):
    """DataCore backend implementation."""

    backend_type = "datacore"
    display_name = "DataCore"

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-datacore"

    @property
    def charm_channel(self) -> str:
        """Return the default charm channel."""
        return "2025.1/edge"

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
        return DatacoreConfig
