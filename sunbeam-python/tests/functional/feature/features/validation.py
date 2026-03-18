# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for validation feature."""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class ValidationTest(BaseFeatureTest):
    """Test validation feature enablement/disablement."""

    feature_name = "validation"
    expected_applications: list[str] = []
    timeout_seconds = 900

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the validation CLI is usable."""
        logger.info("Verifying validation feature via `sunbeam validation profiles`...")
        try:
            subprocess.run(
                ["sunbeam", "validation", "profiles"],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying validation feature: %s", exc)
            raise AssertionError(
                f"Validation feature verification failed: {exc}"
            ) from exc

        logger.info("Validation feature verified via `sunbeam validation profiles`")
