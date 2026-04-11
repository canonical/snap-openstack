# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Dell PowerMax backend implementation using base step classes."""

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


class Protocol(StrEnum):
    """Enumeration of valid protocol types."""

    FC = "fc"
    ISCSI = "iscsi"


class DellpowermaxConfig(StorageBackendConfig):
    """Configuration model for Dell PowerMax backend.

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
        Literal["fc", "iscsi"] | None,
        Field(description="Protocol selector: fc, iscsi."),
    ] = None
    interval: Annotated[
        int | None,
        Field(
            description="Use this value to specify length of the interval in seconds."
        ),
    ] = None
    retries: Annotated[
        int | None, Field(description="Use this value to specify number of retries.")
    ] = None
    initiator_check: Annotated[
        bool | None, Field(description="Use this value to enable the initiator_check.")
    ] = None
    vmax_workload: Annotated[
        str | None,
        Field(
            description="Workload, setting this as an extra spec in pool_name is preferable."  # noqa: E501
        ),
    ] = None
    u4p_failover_timeout: Annotated[
        int | None,
        Field(
            description="How long to wait for the server to send data before giving up."
        ),
    ] = None
    u4p_failover_retries: Annotated[
        int | None,
        Field(
            description="The maximum number of retries each connection should attempt."
        ),
    ] = None
    u4p_failover_backoff_factor: Annotated[
        int | None,
        Field(
            description="A backoff factor to apply between attempts after the second try."  # noqa: E501
        ),
    ] = None
    u4p_failover_autofailback: Annotated[
        bool | None,
        Field(
            description="If the driver should automatically failback to the primary instance of Unisphere."  # noqa: E501
        ),
    ] = None
    u4p_failover_target: Annotated[
        str | None, Field(description="Dictionary of Unisphere failover target info.")
    ] = None
    powermax_array: Annotated[
        str | None, Field(description="Serial number of the array to connect to.")
    ] = None
    powermax_srp: Annotated[
        str | None,
        Field(description="Storage resource pool on array to use for provisioning."),
    ] = None
    powermax_service_level: Annotated[
        str | None, Field(description="Service level to use for provisioning storage.")
    ] = None
    powermax_port_groups: Annotated[
        str | None,
        Field(
            description="List of port groups containing frontend ports configured prior for server connection."  # noqa: E501
        ),
    ] = None
    powermax_array_tag_list: Annotated[
        str | None, Field(description="List of user assigned name for storage array.")
    ] = None
    powermax_short_host_name_template: Annotated[
        str | None, Field(description="User defined override for short host name.")
    ] = None
    powermax_port_group_name_template: Annotated[
        str | None, Field(description="User defined override for port group name.")
    ] = None
    load_balance: Annotated[
        bool | None,
        Field(description="Enable/disable load balancing for a PowerMax backend."),
    ] = None
    load_balance_real_time: Annotated[
        bool | None,
        Field(
            description="Enable/disable real-time performance metrics for Port level load balancing for a PowerMax backend."  # noqa: E501
        ),
    ] = None
    load_data_format: Annotated[
        str | None,
        Field(
            description="Performance data format, not applicable for real-time metrics."
        ),
    ] = None
    load_look_back: Annotated[
        int | None,
        Field(
            description="How far in minutes to look back for diagnostic performance metrics in load calculation."  # noqa: E501
        ),
    ] = None
    load_look_back_real_time: Annotated[
        int | None,
        Field(
            description="How far in minutes to look back for real-time performance metrics in load calculation."  # noqa: E501
        ),
    ] = None
    port_group_load_metric: Annotated[
        str | None, Field(description="Metric used for port group load calculation.")
    ] = None
    port_load_metric: Annotated[
        str | None, Field(description="Metric used for port load calculation.")
    ] = None
    rest_api_connect_timeout: Annotated[
        int | None,
        Field(
            description="Use this value to specify connect timeout value (in seconds) for rest call."  # noqa: E501
        ),
    ] = None
    rest_api_read_timeout: Annotated[
        int | None,
        Field(
            description="Use this value to specify read timeout value (in seconds) for rest call."  # noqa: E501
        ),
    ] = None
    snapvx_unlink_symforce: Annotated[
        bool | None,
        Field(
            description="Enable SnapVx unlink symforce, which forces the operation to execute when normally it is rejected."  # noqa: E501
        ),
    ] = None
    san_thin_provision: Annotated[
        bool | None, Field(description="Use thin provisioning for SAN volumes?")
    ] = None
    use_multipath_for_image_xfer: Annotated[
        bool | None,
        Field(description="Enable multipathing for image transfer operations."),
    ] = None


class DellpowermaxBackend(StorageBackendBase):
    """Dell PowerMax backend implementation."""

    backend_type = "dellpowermax"
    display_name = "Dell PowerMax"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-dellpowermax"

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
        return True

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration model type for this backend."""
        return DellpowermaxConfig
