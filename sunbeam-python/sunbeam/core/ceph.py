# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Abstract interface for Ceph storage providers.

This module defines the contract between the Sunbeam core orchestration
code and the storage infrastructure that provides Ceph services.

The currently only implementation is MicrocephProvider (local Ceph via
the microceph charm). Future implementations could connect to remote or
external Ceph clusters without deploying microceph locally.
"""

from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sunbeam.core.deployment import Deployment

LOG = logging.getLogger(__name__)


class CephProvider(abc.ABC):
    """Abstract interface for Ceph storage infrastructure providers.

    This defines the contract between the Sunbeam orchestration code
    and whatever provides Ceph services (microceph, remote ceph, etc.).

    Implementations are responsible for providing terraform variables
    that the control plane and cinder-volume steps need.
    """

    @abc.abstractmethod
    def get_control_plane_tfvars(
        self,
        model_with_owner: str,
        storage_node_count: int,
    ) -> dict[str, Any]:
        """Return Ceph-related terraform variables for the control plane.

        Called by DeployControlPlaneStep / ReapplyControlPlaneStep to
        compute the ceph portion of the openstack terraform variables.

        :param model_with_owner: Juju model name with owner prefix
            (e.g. "admin/openstack-machines").
        :param storage_node_count: Number of nodes with the storage role.
        :returns: dict with at minimum:
            - enable-ceph: bool
            - ceph-offer-url: str (when enabled)
            - ceph-nfs-offer-url: str (when enabled)
            - ceph-rgw-offer-url: str (when enabled)
            - ceph-osd-replication-count: int (when enabled)
        """

    @abc.abstractmethod
    def get_cinder_volume_tfvars(
        self,
        deployment: Deployment,
        storage_node_count: int,
    ) -> dict[str, Any]:
        """Return Ceph-related terraform variables for cinder-volume.

        Called by DeployCinderVolumeApplicationStep to configure the
        cinder-volume-ceph subordinate charm.

        :param deployment: The active deployment (for accessing terraform helpers).
        :param storage_node_count: Number of nodes with the storage role.
        :returns: dict with at minimum:
            - charm_cinder_volume_ceph_config: dict (ceph-osd-replication-count)
            - ceph-application-name: str (when storage nodes exist)
        """

    @abc.abstractmethod
    def get_replica_count(self, storage_node_count: int) -> int:
        """Return the Ceph OSD replication count.

        :param storage_node_count: Number of nodes with the storage role.
        """

    @property
    @abc.abstractmethod
    def application_name(self) -> str:
        """Return the Juju application name (e.g. 'microceph')."""

    @property
    @abc.abstractmethod
    def status_column(self) -> str:
        """Return the column name for cluster status display."""

    @property
    @abc.abstractmethod
    def terraform_plan_name(self) -> str:
        """Return the terraform plan key (e.g. 'microceph-plan')."""
