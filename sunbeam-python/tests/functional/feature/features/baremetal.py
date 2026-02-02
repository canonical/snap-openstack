# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for baremetal feature.

Baremetal provides Ironic-based bare metal provisioning.
Functionality is validated via the Ironic (baremetal) API.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class BaremetalTest(BaseFeatureTest):
    """Test baremetal feature enablement/disablement."""

    feature_name = "baremetal"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the Baremetal (Ironic) API is reachable."""
        logger.info("Verifying Baremetal (Ironic) service is available...")
        try:
            subprocess.run(
                ["openstack", "baremetal", "driver", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying Baremetal service: %s", exc)
            raise AssertionError(
                f"Baremetal service verification failed: {exc}"
            ) from exc

        logger.info("Baremetal service verified via `openstack baremetal driver list`")
