# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Functional smoke test for ``sunbeam backup``.

Skipped by default: requires a bootstrapped cloud with the ``openstack`` snap
installed and the object-storage backup prerequisite configured. Consistent with
the rest of the hardware/environment-gated functional suite.
"""

import os

import pytest

from .utils import sunbeam_command

pytestmark = pytest.mark.skipif(
    not os.environ.get("SUNBEAM_FUNCTIONAL_BACKUP"),
    reason="requires a bootstrapped cloud; set SUNBEAM_FUNCTIONAL_BACKUP to enable",
)


def test_backup_smoke():
    """``sunbeam backup --help`` is available and the command runs."""
    output = sunbeam_command("backup --help", capture_output=True)
    assert "Create backups" in output
