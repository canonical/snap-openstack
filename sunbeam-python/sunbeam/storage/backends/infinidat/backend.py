# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Infinidat InfiniBox storage backend implementation using base step classes."""

import logging
from typing import Annotated, Any, Literal

from pydantic import Field

from sunbeam.core.deployment import Deployment
from sunbeam.core.manifest import Manifest, StorageBackendConfig
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import SecretDictField

LOG = logging.getLogger(__name__)


class InfinidatConfig(StorageBackendConfig):
    """Static configuration model for Infinidat storage backend."""

    # Mandatory connection parameters
    san_ip: Annotated[str, Field(description="InfiniBox Management IP")]
    infinidat_pool_name: Annotated[str, Field(description="InfiniBox Pool Name")]
    protocol: Annotated[
        Literal["iscsi", "fc"] | None,
        Field(description="Storage Protocol (iscsi or fc)"),
    ] = "iscsi"
    infinidat_iscsi_netspaces: Annotated[
        str | None, Field(description="Comma-separated list of iSCSI network spaces")
    ] = None
    use_chap_auth: Annotated[
        bool | None, Field(description="Use CHAP authentication")
    ] = True

    # Secrets
    san_login: Annotated[
        str,
        Field(description="InfiniBox Username"),
        SecretDictField(field="san-login"),
    ]
    san_password: Annotated[
        str,
        Field(description="InfiniBox Password"),
        SecretDictField(field="san-password"),
    ]
    chap_username: Annotated[
        str | None,
        Field(description="CHAP Username"),
        SecretDictField(field="chap-username"),
    ] = None
    chap_password: Annotated[
        str | None,
        Field(description="CHAP Password"),
        SecretDictField(field="chap-password"),
    ] = None

    # Storage provisioning and management
    infinidat_use_compression: Annotated[
        bool | None,
        Field(description="Enable InfiniBox volume compression"),
    ] = None
    max_over_subscription_ratio: Annotated[
        float | None,
        Field(description="Maximum oversubscription ratio for thin provisioning"),
    ] = None


class InfinidatBackend(StorageBackendBase):
    """Infinidat Cinder Backend."""

    backend_type = "infinidat"
    display_name = "Infinidat"

    @property
    def charm_name(self) -> str:
        """Return the charm name for this backend."""
        return "cinder-volume-infinidat"

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
        """Return the configuration class for Infinidat backend."""
        return InfinidatConfig
