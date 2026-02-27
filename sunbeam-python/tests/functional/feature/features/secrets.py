# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for secrets feature."""

import logging
import subprocess

import pytest

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class SecretsTest(BaseFeatureTest):
    """Test secrets feature enablement/disablement."""

    feature_name = "secrets"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def _ensure_vault_enabled(self) -> bool:
        """Ensure the Vault feature is enabled before Secrets."""
        logger.info("Ensuring 'vault' feature is enabled before 'secrets'...")
        try:
            self.sunbeam.enable_feature("vault")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to enable required dependency 'vault': %s", exc)
            return False
        return True

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the Secrets (Barbican) API is reachable."""
        logger.info("Verifying Secrets (Barbican) service is available...")
        try:
            subprocess.run(
                ["openstack", "secret", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying Secrets service: %s", exc)
            raise AssertionError(f"Secrets service verification failed: {exc}") from exc

        logger.info("Secrets service verified via `openstack secret list`")

    def run_full_lifecycle(self) -> bool:
        """Enable Vault first, then run the Secrets lifecycle."""
        if not self._ensure_vault_enabled():
            pytest.skip(
                "Skipping Secrets feature test: dependency 'vault' not available"
            )

        return super().run_full_lifecycle()
