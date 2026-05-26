# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""VStorage backend implementation using base step classes."""

import logging
from enum import StrEnum
from typing import Annotated

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()


class Protocol(StrEnum):
    """Enumeration of valid protocol types."""

    FC = "fc"
    ISCSI = "iscsi"


class NecvConfig(StorageBackendConfig):
    """Configuration model for VStorage backend.

    This model includes ALL configuration options for the backend.
    Additional configuration can be managed dynamically through the charm.
    """

    # Mandatory connection parameters
    san_ip: Annotated[
        str, Field(description="Storage array management IP address or hostname")
    ]

    # Optional backend configuration
    protocol: Annotated[
        Protocol | None,
        Field(description="Protocol selector: fc, iscsi."),
    ] = None
    nec_v_storage_id: Annotated[
        str | None, Field(description="Product number of the storage system.")
    ] = None
    nec_v_pools: Annotated[
        str | None, Field(description="Pool number[s] or pool name[s] of the DP pool.")
    ] = None
    nec_v_snap_pool: Annotated[
        str | None, Field(description="Pool number or pool name of the snapshot pool.")
    ] = None
    nec_v_ldev_range: Annotated[
        str | None,
        Field(
            description=(
                "Range of the LDEV numbers in the format of "
                "'xxxx-yyyy' that can be used by the driver."
            )
        ),
    ] = None
    nec_v_target_ports: Annotated[
        str | None,
        Field(
            description=(
                "IDs of the storage ports used to attach volumes "
                "to the controller node."
            )
        ),
    ] = None
    nec_v_compute_target_ports: Annotated[
        str | None,
        Field(
            description=(
                "IDs of the storage ports used to attach volumes to compute nodes."
            )
        ),
    ] = None
    nec_v_group_create: Annotated[
        bool | None,
        Field(
            description=(
                "If True, the driver will create host groups or iSCSI "
                "targets on storage ports as needed."
            )
        ),
    ] = None
    nec_v_group_delete: Annotated[
        bool | None,
        Field(
            description=(
                "If True, the driver will delete host groups or iSCSI "
                "targets on storage ports as needed."
            )
        ),
    ] = None
    nec_v_copy_speed: Annotated[
        int | None,
        Field(
            description=(
                "Copy speed of storage system. 1 or 2 indicates low speed, "
                "3 indicates middle speed, and a value between 4 and 15 "
                "indicates high speed."
            )
        ),
    ] = None
    nec_v_copy_check_interval: Annotated[
        int | None,
        Field(
            description=(
                "Interval in seconds to check copying status during a volume copy."
            )
        ),
    ] = None
    nec_v_async_copy_check_interval: Annotated[
        int | None,
        Field(
            description=(
                "Interval in seconds to check asynchronous copying status "
                "during copy pair deletion or data restoration."
            )
        ),
    ] = None
    nec_v_manage_drs_volumes: Annotated[
        bool | None,
        Field(
            description=(
                "If true, the driver creates a driver-managed vClone "
                "parent for each non-cloned DRS volume."
            )
        ),
    ] = None
    nec_v_rest_disable_io_wait: Annotated[
        bool | None,
        Field(
            description=(
                "It may take time to detach volume after I/O. This option "
                "allows detaching volume to complete immediately."
            )
        ),
    ] = None
    nec_v_rest_tcp_keepalive: Annotated[
        bool | None,
        Field(description="Enables or disables use of REST API tcp keepalive"),
    ] = None
    nec_v_discard_zero_page: Annotated[
        bool | None,
        Field(description="Enable or disable zero page reclamation in a DP-VOL."),
    ] = None
    nec_v_lun_timeout: Annotated[
        int | None,
        Field(description="Maximum wait time in seconds for adding a LUN to complete."),
    ] = None
    nec_v_lun_retry_interval: Annotated[
        int | None,
        Field(description="Retry interval in seconds for REST API adding a LUN."),
    ] = None
    nec_v_restore_timeout: Annotated[
        int | None,
        Field(
            description=(
                "Maximum wait time in seconds for the restore operation to complete."
            )
        ),
    ] = None
    nec_v_state_transition_timeout: Annotated[
        int | None,
        Field(
            description=(
                "Maximum wait time in seconds for a volume transition to complete."
            )
        ),
    ] = None
    nec_v_lock_timeout: Annotated[
        int | None,
        Field(description="Maximum wait time in seconds for storage to be unlocked."),
    ] = None
    nec_v_rest_timeout: Annotated[
        int | None,
        Field(
            description=(
                "Maximum wait time in seconds for REST API execution to complete."
            )
        ),
    ] = None
    nec_v_extend_timeout: Annotated[
        int | None,
        Field(
            description=(
                "Maximum wait time in seconds for a volume extension to complete."
            )
        ),
    ] = None
    nec_v_exec_retry_interval: Annotated[
        int | None,
        Field(description="Retry interval in seconds for REST API execution."),
    ] = None
    nec_v_rest_connect_timeout: Annotated[
        int | None,
        Field(
            description=(
                "Maximum wait time in seconds for REST API connection to complete."
            )
        ),
    ] = None
    nec_v_rest_job_api_response_timeout: Annotated[
        int | None,
        Field(description="Maximum wait time in seconds for a response from REST API."),
    ] = None
    nec_v_rest_get_api_response_timeout: Annotated[
        int | None,
        Field(
            description=(
                "Maximum wait time in seconds for a response "
                "against GET method of REST API."
            )
        ),
    ] = None
    nec_v_rest_server_busy_timeout: Annotated[
        int | None,
        Field(description="Maximum wait time in seconds when REST API returns busy."),
    ] = None
    nec_v_rest_keep_session_loop_interval: Annotated[
        int | None,
        Field(description="Loop interval in seconds for keeping REST API session."),
    ] = None
    nec_v_rest_another_ldev_mapped_retry_timeout: Annotated[
        int | None,
        Field(
            description="Retry time in seconds when new LUN allocation request fails."
        ),
    ] = None
    nec_v_rest_tcp_keepidle: Annotated[
        int | None,
        Field(
            description="Wait time in seconds for sending a first TCP keepalive packet."
        ),
    ] = None
    nec_v_rest_tcp_keepintvl: Annotated[
        int | None,
        Field(
            description="Interval of transmissions in seconds for TCP keepalive packet."
        ),
    ] = None
    nec_v_rest_tcp_keepcnt: Annotated[
        int | None,
        Field(description="Maximum number of transmissions for TCP keepalive packet."),
    ] = None
    nec_v_host_mode_options: Annotated[
        str | None, Field(description="Host mode option for host group or iSCSI target")
    ] = None
    nec_v_zoning_request: Annotated[
        bool | None,
        Field(
            description=(
                "If True, the driver will configure FC zoning between "
                "the server and storage system when FC zoning manager "
                "is enabled."
            )
        ),
    ] = None


class NecvBackend(StorageBackendBase):
    """VStorage backend implementation."""

    backend_type = "necv"
    display_name = "VStorage"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm application name."""
        return "cinder-volume-necv"

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
        return NecvConfig
