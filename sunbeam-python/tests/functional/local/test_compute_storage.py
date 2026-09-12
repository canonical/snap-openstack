# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Functional tests for encrypted compute storage.

These tests require a local Sunbeam deployment and an operator-prepared
LUKS device. They are skipped unless the required environment variables
are provided.
"""

import logging
import os
import subprocess

import pytest

from . import utils

LOG = logging.getLogger(__name__)

CONFIG_ENV = "SUNBEAM_FUNCTIONAL_COMPUTE_STORAGE_CONFIG"
DEVICE_ENV = "SUNBEAM_FUNCTIONAL_ENCRYPTED_DEVICE"


def _run(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def test_configure_compute_storage(ensure_local_cluster_bootstrapped):
    """Enroll and mount an operator-prepared encrypted device."""
    config_path = os.environ.get(CONFIG_ENV)
    device = os.environ.get(DEVICE_ENV)
    if not config_path or not device:
        pytest.skip(
            f"Set {CONFIG_ENV} and {DEVICE_ENV} to run encrypted storage "
            "functional tests"
        )

    LOG.info("Configuring encrypted compute storage from %s", config_path)
    utils.sunbeam_command(f"configure compute-storage --config {config_path}")

    luks_uuid = _run(["cryptsetup", "luksUUID", device])
    assert luks_uuid, "LUKS device has no UUID"

    mapper = f"/dev/mapper/crypt-{luks_uuid}"
    assert os.path.exists(mapper), f"Mapper {mapper} was not opened"

    status = _run(["cryptsetup", "status", mapper])
    assert "is active" in status, f"Mapper {mapper} is not active"
