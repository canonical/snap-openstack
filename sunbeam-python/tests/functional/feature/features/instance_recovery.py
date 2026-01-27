# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for instance-recovery feature."""

import subprocess

from .base import BaseFeatureTest


class InstanceRecoveryTest(BaseFeatureTest):
    """Test instance-recovery feature enablement/disablement."""

    # CLI feature name
    feature_name = "instance-recovery"
    expected_applications = [
        "masakari",
        "masakari-mysql-router",
        "consul-management",
        "consul-storage",
        "consul-tenant",
    ]
    expected_units = [
        "masakari/0",
        "masakari-mysql-router/0",
        "consul-management/0",
        "consul-storage/0",
        "consul-tenant/0",
    ]
    timeout_seconds = 900

    def validate_feature_behavior(self) -> None:
        """Run a small smoke test against the Masakari API.

        We call `openstack segment list` to confirm Masakari is responding
        and that the CLI can talk to the Instance Recovery control plane.
        """
        cmd = [
            "openstack",
            "segment",
            "list",
            "-c",
            "name",
            "-c",
            "service_type",
        ]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if not result.stdout.strip():
            raise AssertionError("openstack segment list returned no data")

    def run_full_lifecycle(self) -> bool:
        """Enable instance-recovery, verify resources and behavior, then disable it."""
        self.enable()
        self.validate_feature_behavior()
        self.disable()
        return True
