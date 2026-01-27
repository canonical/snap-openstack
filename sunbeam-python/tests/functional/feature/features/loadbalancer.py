# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for loadbalancer feature.

Loadbalancer is a simple feature with no dependencies.
Deploys Octavia, the OpenStack Load Balancer as a Service.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class LoadbalancerTest(BaseFeatureTest):
    """Test loadbalancer feature enablement/disablement."""

    feature_name = "loadbalancer"
    expected_applications: list[str] = ["octavia"]
    expected_units: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that loadbalancer service (Octavia) is working."""
        logger.info("Verifying loadbalancer service (Octavia) is available...")

        try:
            result = subprocess.run(
                ["openstack", "loadbalancer", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            logger.info("Loadbalancer service (Octavia) is accessible")
            logger.debug("Loadbalancer list output: %s", result.stdout[:200])

        except Exception as e:
            logger.warning("Error checking loadbalancer service: %s", e)
            raise AssertionError(f"Loadbalancer service verification failed: {e}")

    def run_full_lifecycle(self) -> bool:
        """Enable loadbalancer, perform basic test, then disable it."""
        logger.info("Starting lifecycle test for feature: '%s'", self.feature_name)

        self.enable()
        self.verify_validate_feature_behavior()

        disable_success = self.disable()
        if not disable_success:
            logger.warning("Loadbalancer disable failed, but continuing test sequence")

        return True
