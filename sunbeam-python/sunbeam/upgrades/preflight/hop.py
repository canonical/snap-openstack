# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Active hop creation after preflight checks pass.

This module bridges the preflight checks and the coordinator: after
all checks pass, it creates the active hop atomically and copies the
orchestration metadata to clusterd so it's available to all nodes.

The hop is created with status ``pending``. The first mutating
command (control-plane, dataplane, storage) transitions it to
``in_progress``.
"""

from __future__ import annotations

import logging

from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.upgrades.coordinator import ReleaseUpgradeCoordinator
from sunbeam.upgrades.metadata import HopMetadata
from sunbeam.upgrades.observability import UpgradeLogger
from sunbeam.upgrades.state import Hop

LOG = logging.getLogger(__name__)

# clusterd config key where the orchestration metadata is copied so
# all nodes can read it during the upgrade.
UPGRADE_METADATA_KEY = "upgrade_metadata"


def _get_snap_revision() -> str:
    """Return the current snap revision."""
    return Snap().revision


def create_hop_after_preflight(
    client: Client,
    from_release: str,
    to_release: str,
    metadata: HopMetadata,
) -> Hop:
    """Create the active hop after all preflight checks pass.

    Acquires the upgrade lock, creates the hop in persisted state with
    status ``pending``, copies the orchestration metadata to clusterd,
    and releases the lock. The hop is ready for the first mutating
    command to transition it to ``in_progress``.

    :param client: clusterd client
    :param from_release: source release (e.g. "2025.1")
    :param to_release: target release (e.g. "2026.1")
    :param metadata: the loaded orchestration metadata for this hop
    :returns: the newly created Hop
    :raises UpgradeLockHeldException: if the lock is held by another process
    """
    coordinator = ReleaseUpgradeCoordinator(client, UpgradeLogger())

    try:
        coordinator.acquire_lock()
        coordinator.load_state()

        if coordinator.get_current_hop() is not None:
            raise RuntimeError(
                "An active hop already exists. Abandon it before "
                "starting a new upgrade."
            )

        metadata_build_id = _get_snap_revision()
        hop = coordinator.create_hop(from_release, to_release, metadata_build_id)

        # Copy metadata to clusterd so all nodes can read it
        client.cluster.update_config(
            UPGRADE_METADATA_KEY,
            metadata.model_dump(by_alias=True),
        )

        coordinator.persist_state()
        LOG.info(
            "created hop %s -> %s (build_id=%s)",
            from_release,
            to_release,
            metadata_build_id,
        )
        return hop
    finally:
        coordinator.release_lock()
