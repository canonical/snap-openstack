# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from snaphelpers import Snap

from sunbeam.log import setup_logging

if TYPE_CHECKING:
    from sunbeam.clusterd.client import Client

LOG = logging.getLogger(__name__)
DEFAULT_CONFIG = {
    "daemon.group": "snap_daemon",
    "daemon.debug": False,
    "k8s.provider": "k8s",
    "deployment.risk": "stable",
    "deployment.version": "2024.1",
}

OPTION_KEYS = {k.split(".")[0] for k in DEFAULT_CONFIG.keys()}


def _update_default_config(snap: Snap) -> None:
    """Add any missing default configuration keys.

    :param snap: the snap reference
    """
    current_options = snap.config.get_options(*OPTION_KEYS)
    for option, default in DEFAULT_CONFIG.items():
        if option not in current_options:
            snap.config.set({option: default})


def _write_config(path: Path, config: dict) -> None:
    """Write the configuration to the specified path.

    :param path: the path to write the configuration to
    :param config: the configuration to write
    """
    with path.open("w") as fp:
        json.dump(config, fp)


def _read_config(path: Path) -> dict:
    """Read the configuration from the specified path.

    :param path: the path to read the configuration from
    :return: the configuration
    """
    if not path.exists():
        return {}
    with path.open("r") as fp:
        return json.load(fp) or {}


def sync_feature_gates_from_snap_to_cluster(
    client: "Client", snap: Snap | None = None
) -> None:
    """Sync feature gates from snap config to cluster database.

    This is a one-way sync: local snap config → cluster DB.
    The sunbeam-microcluster daemon watches the cluster DB and pushes
    changes to all nodes' snap configs automatically.

    Flow:
    1. User runs: snap set openstack feature.X=true (on any node)
    2. This function pushes change to cluster DB
    3. Daemon on ALL nodes detects DB change
    4. Daemon runs: snapctl set feature.X=true (on each node)
    5. All nodes synchronized automatically

    :param client: cluster client instance
    :param snap: optional snap reference (if None, will use snaphelpers to read config)
    """
    from sunbeam.clusterd.service import ClusterServiceUnavailableException

    try:
        # Read all feature.* keys from local snap config
        if snap:
            feature_options = snap.config.get_options("feature")
        else:
            # Use snaphelpers to read config when snap object not available
            import snaphelpers

            snap_helper = snaphelpers.Snap()
            feature_options = snap_helper.config.get_options("feature")

        if not feature_options:
            LOG.debug("No feature gates found in snap config")
            return

        feature_dict = feature_options.as_dict()
        for key, value in feature_dict.items():
            # Skip nested objects - only process leaf values
            if isinstance(value, dict):
                LOG.debug(f"Skipping nested structure: {key}")
                continue

            gate_key = key if key.startswith("feature.") else f"feature.{key}"
            enabled_bool = bool(value) if value is not None else False

            try:
                # Check if gate exists in cluster
                existing_gate = client.cluster.get_feature_gate(gate_key)

                # Update if value changed
                if existing_gate.enabled != enabled_bool:
                    client.cluster.update_feature_gate(gate_key, enabled_bool)
                    LOG.info(
                        f"Updated feature gate '{gate_key}' in cluster "
                        f"(changed from {existing_gate.enabled} to {enabled_bool})"
                    )
            except Exception:
                # Create if doesn't exist
                try:
                    client.cluster.add_feature_gate(gate_key, enabled_bool)
                    LOG.info(
                        f"Created feature gate '{gate_key}' in cluster "
                        f"(enabled={enabled_bool})"
                    )
                except Exception as e:
                    LOG.warning(f"Failed to sync feature gate '{gate_key}': {e}")

    except ClusterServiceUnavailableException:
        LOG.debug("Cluster service unavailable, skipping feature gate sync")
    except Exception as e:
        LOG.warning(f"Failed to sync feature gates to cluster: {e}")


def _sync_feature_gates_to_cluster(snap: Snap) -> None:
    """Hook-specific wrapper for syncing feature gates.

    This is called from the configure hook and passes the snap object.

    :param snap: the snap reference
    """
    from sunbeam.clusterd.client import Client

    try:
        client = Client.from_socket()
    except Exception:
        # Cluster not initialized yet (single-node or bootstrap phase)
        LOG.debug("Cluster not available, skipping feature gate sync")
        return

    sync_feature_gates_from_snap_to_cluster(client, snap)


def install(snap: Snap) -> None:
    """Runs the 'install' hook for the snap.

    The 'install' hook will create the configuration and bundle deployment
    directories inside of $SNAP_COMMON as well as setting the default
    configuration options for the snap.

    :param snap: the snap instance
    :type snap: Snap
    :return:
    """
    setup_logging(snap.paths.common / "hooks.log")
    LOG.debug("Running install hook...")
    logging.info(f"Setting default config: {DEFAULT_CONFIG}")
    snap.config.set(DEFAULT_CONFIG)


def upgrade(snap: Snap) -> None:
    """Runs the 'upgrade' hook for the snap.

    The 'upgrade' hook will upgrade the various bundle information, etc. This
    is

    :param snap: the snap reference
    """
    setup_logging(snap.paths.common / "hooks.log")
    LOG.debug("Running the upgrade hook...")


def configure(snap: Snap) -> None:
    """Runs the `configure` hook for the snap.

    This method is invoked when the configure hook is executed by the snapd
    daemon. The `configure` hook is invoked when the user runs a sudo snap
    set openstack.<foo> setting.

    For feature gates, this hook syncs the local snap configuration to the
    cluster database, ensuring all nodes in a multi-node deployment see
    the same feature gate settings.

    :param snap: the snap reference
    """
    setup_logging(snap.paths.common / "hooks.log")
    logging.info("Running configure hook")

    _update_default_config(snap)

    # Sync feature gates to cluster database for multi-node consistency
    _sync_feature_gates_to_cluster(snap)

    config_path = snap.paths.data / "config.yaml"
    old_config = _read_config(config_path)
    new_config = snap.config.get_options(*OPTION_KEYS).as_dict()
    _write_config(config_path, new_config)
    if old_config.get("daemon") != new_config.get("daemon"):
        snap.services.list()["clusterd"].restart()
