# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for images-sync feature.

Images-sync is a simple feature with no dependencies.
Functionality is validated via the OpenStack Image API.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class ImagesSyncTest(BaseFeatureTest):
    """Test images-sync feature enablement/disablement."""

    feature_name = "images-sync"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the Image service is reachable.

        We call `openstack image list` to confirm that Glance is responding.
        """
        logger.info("Verifying Image service (Glance) is available...")
        try:
            subprocess.run(
                ["openstack", "image", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying Image service: %s", exc)
            raise AssertionError(f"Image service verification failed: {exc}") from exc

        logger.info("Image service verified via `openstack image list`")
