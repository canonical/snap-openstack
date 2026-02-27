# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for vault feature.

Vault provides the HashiCorp Vault service used by other features.
Functionality is validated via the `sunbeam vault status` command.
"""

import logging

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class VaultTest(BaseFeatureTest):
    """Test vault feature enablement/disablement."""

    feature_name = "vault"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that Vault is reachable via sunbeam."""
        logger.info("Verifying Vault status via `sunbeam vault status`...")
        try:
            self.sunbeam.run(["vault", "status"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying Vault service: %s", exc)
            raise AssertionError(f"Vault service verification failed: {exc}") from exc

        logger.info("Vault service verified via `sunbeam vault status`")
