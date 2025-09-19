# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from pathlib import Path

from snaphelpers import Snap

from sunbeam.log import setup_logging

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

    :param snap: the snap reference
    """
    setup_logging(snap.paths.common / "hooks.log")
    logging.info("Running configure hook")

    _update_default_config(snap)

    config_path = snap.paths.data / "config.yaml"
    old_config = _read_config(config_path)
    new_config = snap.config.get_options(*OPTION_KEYS).as_dict()
    _write_config(config_path, new_config)
    if old_config.get("daemon") != new_config.get("daemon"):
        snap.services.list()["clusterd"].restart()
