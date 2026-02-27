# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for orchestration feature.

Orchestration is a simple feature with no dependencies.
Deploys Heat, the OpenStack Orchestration service.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class OrchestrationTest(BaseFeatureTest):
    """Test orchestration feature enablement/disablement."""

    feature_name = "orchestration"
    expected_applications: list[str] = ["heat"]
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that orchestration service (Heat) is working."""
        logger.info("Verifying orchestration service (Heat) is available...")

        try:
            result = subprocess.run(
                ["openstack", "stack", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            logger.info("Orchestration service (Heat) is accessible")
            logger.debug("Stack list output: %s", result.stdout[:200])

        except Exception as e:
            logger.warning("Error checking orchestration service: %s", e)
            raise AssertionError(f"Orchestration service verification failed: {e}")
