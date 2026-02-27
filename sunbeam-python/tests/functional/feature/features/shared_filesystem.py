# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for shared-filesystem feature.

Shared Filesystems provides Manila-based file share services.
Functionality is validated via the Manila API.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class SharedFilesystemTest(BaseFeatureTest):
    """Test shared-filesystem feature enablement/disablement."""

    feature_name = "shared-filesystem"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the Shared Filesystems (Manila) API is reachable.

        We call `openstack share list` to confirm the API is up.
        """
        logger.info("Verifying Shared Filesystems (Manila) service is available...")
        try:
            subprocess.run(
                ["openstack", "share", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Error while verifying Shared Filesystems service: %s",
                exc,
            )
            raise AssertionError(
                f"Shared Filesystems service verification failed: {exc}"
            ) from exc

        logger.info("Shared Filesystems service verified via `openstack share list`")
