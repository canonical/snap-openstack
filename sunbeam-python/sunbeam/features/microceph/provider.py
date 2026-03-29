# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Microceph implementation of CephProvider.

This provides local Ceph storage via the microceph charm deployed
to the machine model on storage-role nodes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sunbeam.core.ceph import CephProvider
from sunbeam.features.microceph.steps import (
    APPLICATION,
    NFS_OFFER_NAME,
    RGW_OFFER_NAME,
    ceph_replica_scale,
)

if TYPE_CHECKING:
    from sunbeam.core.deployment import Deployment

LOG = logging.getLogger(__name__)

TERRAFORM_PLAN_NAME = "microceph-plan"


class MicrocephProvider(CephProvider):
    """Ceph storage provider using the microceph charm.

    Deploys microceph locally on storage-role nodes. This is the default
    and currently only implementation of CephProvider.
    """

    def get_control_plane_tfvars(
        self,
        model_with_owner: str,
        storage_node_count: int,
    ) -> dict[str, Any]:
        """Return Ceph-related terraform variables for the control plane."""
        tfvars: dict[str, Any] = {}
        if storage_node_count > 0:
            tfvars["enable-ceph"] = True
            tfvars["ceph-offer-url"] = f"{model_with_owner}.{APPLICATION}"
            tfvars["ceph-nfs-offer-url"] = f"{model_with_owner}.{NFS_OFFER_NAME}"
            tfvars["ceph-rgw-offer-url"] = f"{model_with_owner}.{RGW_OFFER_NAME}"
            tfvars["ceph-osd-replication-count"] = ceph_replica_scale(
                storage_node_count
            )
        else:
            tfvars["enable-ceph"] = False
        return tfvars

    def get_cinder_volume_tfvars(
        self,
        deployment: Deployment,
        storage_node_count: int,
    ) -> dict[str, Any]:
        """Return Ceph-related terraform variables for cinder-volume."""
        tfvars: dict[str, Any] = {
            "charm_cinder_volume_ceph_config": {
                "ceph-osd-replication-count": ceph_replica_scale(storage_node_count),
            },
        }

        if storage_node_count > 0:
            microceph_tfhelper = deployment.get_tfhelper(TERRAFORM_PLAN_NAME)
            microceph_tf_output = microceph_tfhelper.output()
            ceph_application_name = microceph_tf_output.get("ceph-application-name")
            if ceph_application_name:
                tfvars["ceph-application-name"] = ceph_application_name

        return tfvars

    def get_replica_count(self, storage_node_count: int) -> int:
        """Return the Ceph OSD replication count."""
        return ceph_replica_scale(storage_node_count)

    @property
    def application_name(self) -> str:
        """Return the microceph Juju application name."""
        return APPLICATION

    @property
    def status_column(self) -> str:
        """Return the column name for cluster status display."""
        return "storage"

    @property
    def terraform_plan_name(self) -> str:
        """Return the terraform plan key."""
        return TERRAFORM_PLAN_NAME
