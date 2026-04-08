# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Datera backend implementation using base step classes."""

import logging
from typing import Annotated, Literal

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class DateraConfig(StorageBackendConfig):
    """Configuration model for Datera backend.

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
        Literal["iscsi"] | None,
        Field(description="Protocol selector: iscsi."),
    ] = None
    datera_ldap_server: Annotated[
        str | None, Field(description="LDAP authentication server")
    ] = None
    datera_503_timeout: Annotated[
        int | None, Field(description="Timeout for HTTP 503 retry messages")
    ] = None
    datera_503_interval: Annotated[
        int | None, Field(description="Interval between 503 retries")
    ] = None
    datera_debug: Annotated[
        bool | None, Field(description="True to set function arg and return logging")
    ] = None
    datera_debug_replica_count_override: Annotated[
        bool | None,
        Field(
            description="ONLY FOR DEBUG/TESTING PURPOSES True to set replica_count to 1"
        ),
    ] = None
    datera_tenant_id: Annotated[
        str | None,
        Field(
            description="If set to 'Map' --> OpenStack project ID will be mapped implicitly to Datera tenant ID"  # noqa: E501
        ),
    ] = None
    datera_enable_image_cache: Annotated[
        bool | None,
        Field(description="Set to True to enable Datera backend image caching"),
    ] = None
    datera_image_cache_volume_type_id: Annotated[
        str | None, Field(description="Cinder volume type id to use for cached volumes")
    ] = None
    datera_disable_profiler: Annotated[
        bool | None,
        Field(description="Set to True to disable profiling in the Datera driver"),
    ] = None
    datera_disable_extended_metadata: Annotated[
        bool | None,
        Field(
            description="Set to True to disable sending additional metadata to the Datera backend"  # noqa: E501
        ),
    ] = None
    datera_disable_template_override: Annotated[
        bool | None,
        Field(
            description="Set to True to disable automatic template override of the size attribute when creating from a template"  # noqa: E501
        ),
    ] = None
    datera_volume_type_defaults: Annotated[
        str | None,
        Field(
            description="Settings here will be used as volume-type defaults if the volume-type setting is not provided."  # noqa: E501
        ),
    ] = None
    san_thin_provision: Annotated[
        bool | None, Field(description="Use thin provisioning for SAN volumes?")
    ] = None
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = None


class DateraBackend(StorageBackendBase):
    """Datera backend implementation."""

    backend_type = "datera"
    display_name = "Datera"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-datera"

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
        return DateraConfig
