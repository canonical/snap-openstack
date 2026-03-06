# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType

LOG = logging.getLogger(__name__)


class SyncFeatureGatesToCluster(BaseStep):
    """Sync feature gates from snap config to cluster database.

    This step is run after cluster initialization (bootstrap or join) to ensure
    that any feature gates set in the snap configuration before the cluster
    was ready are now pushed to the cluster database.

    This handles the case where:
    1. User installs snap
    2. User runs: snap set openstack feature.multi-region=true
    3. User runs: sunbeam cluster bootstrap
    4. This step syncs the feature gate to the cluster DB
    """

    def __init__(self, client: Client):
        super().__init__(
            "Sync feature gates",
            "Syncing feature gates from snap config to cluster database",
        )
        self.client = client

    def run(self, status: Status | None = None) -> Result:
        """Sync feature gates from snap configuration to cluster database."""
        LOG.debug("Syncing snap config feature gates to cluster database")

        try:
            from sunbeam.hooks import sync_feature_gates_from_snap_to_cluster

            sync_feature_gates_from_snap_to_cluster(self.client)
            LOG.info("Successfully synced feature gates from snap config to cluster")
            return Result(ResultType.COMPLETED)

        except Exception as e:
            # Don't fail the step if sync fails - feature gates can be set later
            LOG.warning(f"Failed to sync feature gates to cluster: {e}")
            return Result(
                ResultType.COMPLETED,
                "Feature gate sync failed but continuing (non-fatal)",
            )
