# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Hitachi VSP storage backend implementation using base step classes."""

import logging
from typing import Annotated, Literal

from pydantic import Field
from rich.console import Console

from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)
console = Console()


class HitachiConfig(StorageBackendConfig):
    """Static configuration model for Hitachi VSP storage backend.

    This model includes all configuration options supported by the
    cinder-volume-hitachi charm as defined in charmcraft.yaml.
    """

    # Mandatory connection parameters
    hitachi_storage_id: Annotated[
        str, Field(description="Storage system product number/serial")
    ]
    hitachi_pools: Annotated[
        str, Field(description="Comma-separated list of DP pool names/IDs")
    ]
    san_ip: Annotated[str, Field(description="Hitachi VSP management IP or hostname")]
    san_username: Annotated[
        str,
        Field(description="SAN management username"),
        SecretDictField(field="san-username"),
    ]
    san_password: Annotated[
        str,
        Field(description="SAN management password"),
        SecretDictField(field="san-password"),
    ]
    protocol: Annotated[
        Literal["FC", "iSCSI"], Field(description="Front-end protocol (FC or iSCSI)")
    ]

    # Backend configuration
    volume_backend_name: Annotated[
        str | None, Field(description="Name that Cinder will report for this backend")
    ] = None
    backend_availability_zone: Annotated[
        str | None,
        Field(description="Availability zone to associate with this backend"),
    ] = None

    # Optional host-group / zoning controls
    hitachi_target_ports: Annotated[
        str | None, Field(description="Comma-separated front-end port labels")
    ] = None
    hitachi_compute_target_ports: Annotated[
        str | None, Field(description="Comma-separated compute-node port IDs")
    ] = None
    hitachi_ldev_range: Annotated[
        str | None, Field(description="LDEV range usable by the driver")
    ] = None
    hitachi_zoning_request: Annotated[
        bool | None, Field(description="Request FC zone-manager to create zoning")
    ] = None

    # Copy & replication tuning
    hitachi_copy_speed: Annotated[
        int | None, Field(description="Copy bandwidth throttle (1-15)")
    ] = None
    hitachi_copy_check_interval: Annotated[
        int | None, Field(description="Seconds between sync copy-status polls")
    ] = None
    hitachi_async_copy_check_interval: Annotated[
        int | None, Field(description="Seconds between async copy-status polls")
    ] = None

    # iSCSI authentication
    use_chap_auth: Annotated[
        bool | None, Field(description="Use CHAP authentication for iSCSI")
    ] = None

    # Array ranges and controls
    hitachi_discard_zero_page: Annotated[
        bool | None, Field(description="Enable zero-page reclamation in DP-VOLs")
    ] = None
    hitachi_exec_retry_interval: Annotated[
        int | None, Field(description="Seconds to wait before retrying REST API call")
    ] = None
    hitachi_extend_timeout: Annotated[
        int | None, Field(description="Max seconds to wait for volume extension")
    ] = None
    hitachi_group_create: Annotated[
        bool | None,
        Field(description="Automatically create host groups or iSCSI targets"),
    ] = None
    hitachi_group_delete: Annotated[
        bool | None, Field(description="Automatically delete unused host groups")
    ] = None
    hitachi_group_name_format: Annotated[
        str | None, Field(description="Python format string for naming host groups")
    ] = None
    hitachi_host_mode_options: Annotated[
        str | None, Field(description="Comma-separated host mode options")
    ] = None
    hitachi_lock_timeout: Annotated[
        int | None, Field(description="Max seconds for array login/unlock operations")
    ] = None
    hitachi_lun_retry_interval: Annotated[
        int | None, Field(description="Seconds before retrying LUN mapping")
    ] = None
    hitachi_lun_timeout: Annotated[
        int | None, Field(description="Max seconds to wait for LUN mapping")
    ] = None
    hitachi_port_scheduler: Annotated[
        bool | None, Field(description="Enable round-robin WWN registration")
    ] = None

    # Mirror/replication settings
    hitachi_mirror_compute_target_ports: Annotated[
        str | None, Field(description="Compute-node port names for GAD")
    ] = None
    hitachi_mirror_ldev_range: Annotated[
        str | None, Field(description="LDEV range for secondary storage")
    ] = None
    hitachi_mirror_pair_target_number: Annotated[
        int | None, Field(description="Host group number for GAD on secondary")
    ] = None
    hitachi_mirror_pool: Annotated[
        str | None, Field(description="DP pool name/ID on secondary storage")
    ] = None
    hitachi_mirror_rest_api_ip: Annotated[
        str | None, Field(description="REST API IP on secondary storage")
    ] = None
    hitachi_mirror_rest_api_port: Annotated[
        int | None, Field(description="REST API port on secondary storage")
    ] = None
    hitachi_mirror_rest_pair_target_ports: Annotated[
        str | None, Field(description="Pair-target port names for GAD")
    ] = None
    hitachi_mirror_snap_pool: Annotated[
        str | None, Field(description="Snapshot pool on secondary storage")
    ] = None
    hitachi_mirror_ssl_cert_path: Annotated[
        str | None, Field(description="CA_BUNDLE for secondary REST endpoint")
    ] = None
    hitachi_mirror_ssl_cert_verify: Annotated[
        bool | None, Field(description="Validate SSL cert of secondary REST")
    ] = None
    hitachi_mirror_storage_id: Annotated[
        str | None, Field(description="Product number of secondary storage")
    ] = None
    hitachi_mirror_target_ports: Annotated[
        str | None, Field(description="Controller node port IDs for GAD")
    ] = None
    hitachi_mirror_use_chap_auth: Annotated[
        bool | None, Field(description="Use CHAP auth for GAD on secondary")
    ] = None

    # Replication settings
    hitachi_pair_target_number: Annotated[
        int | None, Field(description="Host group number for primary replication")
    ] = None
    hitachi_path_group_id: Annotated[
        int | None, Field(description="Path group ID for remote replication")
    ] = None
    hitachi_quorum_disk_id: Annotated[
        int | None, Field(description="Quorum disk ID for Global-Active Device")
    ] = None
    hitachi_replication_copy_speed: Annotated[
        int | None, Field(description="Copy speed for remote replication")
    ] = None
    hitachi_replication_number: Annotated[
        int | None, Field(description="Instance number for REST API on replication")
    ] = None
    hitachi_replication_status_check_long_interval: Annotated[
        int | None, Field(description="Poll interval after initial check")
    ] = None
    hitachi_replication_status_check_short_interval: Annotated[
        int | None, Field(description="Initial poll interval")
    ] = None
    hitachi_replication_status_check_timeout: Annotated[
        int | None, Field(description="Max seconds for status change")
    ] = None

    # REST API settings
    hitachi_rest_another_ldev_mapped_retry_timeout: Annotated[
        int | None, Field(description="Retry seconds when LDEV allocation fails")
    ] = None
    hitachi_rest_connect_timeout: Annotated[
        int | None, Field(description="Max seconds to establish REST connection")
    ] = None
    hitachi_rest_disable_io_wait: Annotated[
        bool | None, Field(description="Detach volumes without waiting for I/O drain")
    ] = None
    hitachi_rest_get_api_response_timeout: Annotated[
        int | None, Field(description="Max seconds for sync REST GET")
    ] = None
    hitachi_rest_job_api_response_timeout: Annotated[
        int | None, Field(description="Max seconds for async REST PUT/DELETE")
    ] = None
    hitachi_rest_keep_session_loop_interval: Annotated[
        int | None, Field(description="Seconds between keep-alive loops")
    ] = None
    hitachi_rest_pair_target_ports: Annotated[
        str | None, Field(description="Pair-target port names for REST operations")
    ] = None
    hitachi_rest_server_busy_timeout: Annotated[
        int | None, Field(description="Max seconds when REST API returns busy")
    ] = None
    hitachi_rest_tcp_keepalive: Annotated[
        bool | None, Field(description="Enable TCP keepalive for REST connections")
    ] = None
    hitachi_rest_tcp_keepcnt: Annotated[
        int | None, Field(description="Number of TCP keepalive probes")
    ] = None
    hitachi_rest_tcp_keepidle: Annotated[
        int | None, Field(description="Seconds before sending first TCP keepalive")
    ] = None
    hitachi_rest_tcp_keepintvl: Annotated[
        int | None, Field(description="Seconds between TCP keepalive probes")
    ] = None
    hitachi_rest_timeout: Annotated[
        int | None, Field(description="Max seconds for each REST API call")
    ] = None
    hitachi_restore_timeout: Annotated[
        int | None, Field(description="Max seconds to wait for restore operation")
    ] = None

    # Snapshot settings
    hitachi_snap_pool: Annotated[
        str | None, Field(description="Pool name/ID for snapshots")
    ] = None
    hitachi_state_transition_timeout: Annotated[
        int | None, Field(description="Max seconds for volume state transition")
    ] = None

    chap_username: Annotated[
        str | None,
        Field(description="CHAP username for secret creation"),
        SecretDictField(field="chap-username"),
    ] = None
    chap_password: Annotated[
        str | None,
        Field(description="CHAP password for secret creation"),
        SecretDictField(field="chap-password"),
    ] = None
    hitachi_mirror_chap_username: Annotated[
        str | None,
        Field(description="Mirror CHAP username for secret creation"),
        SecretDictField(field="mirror-chap-username"),
    ] = None
    hitachi_mirror_chap_password: Annotated[
        str | None,
        Field(description="Mirror CHAP password for secret creation"),
        SecretDictField(field="mirror-chap-password"),
    ] = None
    hitachi_mirror_rest_username: Annotated[
        str | None,
        Field(description="Mirror REST username for secret creation"),
        SecretDictField(field="mirror-rest-username"),
    ] = None
    hitachi_mirror_rest_password: Annotated[
        str | None,
        Field(description="Mirror REST password for secret creation"),
        SecretDictField(field="mirror-rest-password"),
    ] = None


class HitachiBackend(StorageBackendBase):
    """Hitachi storage backend implementation."""

    backend_type = "hitachi"
    display_name = "Hitachi VSP Storage"

    @property
    def charm_name(self) -> str:
        """Return the charm name for this backend."""
        return "cinder-volume-hitachi"

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
    def backend_endpoint(self) -> str:
        """Return the backend endpoint for this backend."""
        return "cinder-volume"

    @property
    def units(self) -> int:
        """Return the number of units for this backend."""
        return 1

    @property
    def additional_integrations(self) -> list[str]:
        """Return a list of additional integrations for this backend."""
        return []

    def config_type(self) -> type[StorageBackendConfig]:
        """Return the configuration class for Hitachi backend."""
        return HitachiConfig
