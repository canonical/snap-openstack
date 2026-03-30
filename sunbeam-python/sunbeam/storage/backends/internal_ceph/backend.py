# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Internal Ceph storage backend implementation.

This backend deploys cinder-volume-ceph as a subordinate of the shared HA
cinder-volume principal and wires it to the locally-deployed microceph.

It is managed exclusively by CephFeature and is not exposed via the
``sunbeam storage add`` CLI.
"""

import logging
from typing import Annotated

from pydantic import Field

from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.manifest import StorageBackendConfig
from sunbeam.storage.base import (
    BackendIntegration,
    HypervisorIntegration,
    StorageBackendBase,
)

LOG = logging.getLogger(__name__)


class InternalCephConfig(StorageBackendConfig):
    """Configuration for the internal-ceph storage backend."""

    ceph_osd_replication_count: Annotated[
        int,
        Field(
            default=1,
            description="Ceph OSD replication count",
        ),
    ]


class InternalCephBackend(StorageBackendBase):
    """Internal Ceph storage backend.

    Deploys cinder-volume-ceph as an HA-aware subordinate backend.
    Declares an extra integration to microceph (ceph relation) and a
    hypervisor integration for ceph-access.
    """

    backend_type = "internal-ceph"
    display_name = "Internal Ceph"
    generally_available = True

    @property
    def charm_name(self) -> str:
        """Return the charm name for this backend."""
        return "cinder-volume-ceph"

    @property
    def charm_channel(self) -> str:
        """Return the charm channel for this backend."""
        return "2024.1/stable"

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
        """Return the configuration class for internal ceph backend."""
        return InternalCephConfig

    def get_endpoint_bindings(self, deployment: Deployment) -> list[dict[str, str]]:
        """Endpoint bindings for the cinder-volume-ceph charm.

        Includes the standard default space, ceph-access (MANAGEMENT)
        and ceph (STORAGE) endpoints.
        """
        return [
            {"space": deployment.get_space(Networks.MANAGEMENT)},
            {
                "endpoint": "ceph-access",
                "space": deployment.get_space(Networks.MANAGEMENT),
            },
            {
                "endpoint": "ceph",
                "space": deployment.get_space(Networks.STORAGE),
            },
        ]

    def get_application_name(self, backend_name: str) -> str:
        """Return the Juju application name for the internal Ceph backend."""
        return self.charm_name

    def get_units(self) -> int | None:
        """Return None so Terraform models the subordinate correctly."""
        return None

    def get_extra_integrations(self, deployment: Deployment) -> set[BackendIntegration]:
        """Return the microceph ceph integration."""
        return {
            BackendIntegration(
                application_name="microceph",
                endpoint_name="ceph",
                backend_endpoint_name="ceph",
            )
        }

    def get_hypervisor_integrations(
        self, deployment: Deployment
    ) -> set[HypervisorIntegration]:
        """Return the ceph-access hypervisor integration."""
        return {
            HypervisorIntegration(
                application_name="cinder-volume-ceph",
                endpoint_name="ceph-access",
                hypervisor_endpoint_name="ceph-access",
            )
        }
