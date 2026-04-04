# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Vault feature.

Vault provides the HashiCorp Vault service used by other features.
Functionality is validated via the `sunbeam vault` commands.
"""

import json
import logging
import subprocess
import time
from typing import Optional, Tuple

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


def _ensure_vault_enabled(sunbeam, juju) -> bool:
    """Ensure the Vault feature is enabled and the app exists.

    After ``sunbeam enable vault`` the units are expected to be in a
    blocked state until Vault is initialised, unsealed and authorised,
    so we do *not* wait for an active workload state here.
    """
    if juju.has_application("vault"):
        logger.info("Vault is already enabled")
        return True

    logger.info("Enabling Vault feature...")
    # Use the feature helper so this goes through the same path as other tests.
    sunbeam.enable_feature("vault")
    logger.info("Vault feature enabled")

    logger.info(
        "Waiting for Vault application to appear; units may stay blocked "
        "until initialisation and unsealing are completed."
    )
    juju.wait_for_application("vault", timeout=300)
    return True


def _initialize_vault(sunbeam) -> Tuple[bool, Optional[str], Optional[str]]:
    """Initialise Vault and extract keys per docs (KEY_SHARES KEY_THRESHOLD).

    Returns (success, unseal_key, root_token). If Vault is already
    initialised, sunbeam returns empty JSON and we have no keys to unseal.
    """
    logger.info("Initialising Vault (1 key share, 1 threshold)...")
    sunbeam_cmd = getattr(sunbeam, "_sunbeam_cmd", "sunbeam")

    init_result = subprocess.run(
        [sunbeam_cmd, "vault", "init", "-f", "json", "1", "1"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if init_result.returncode != 0:
        stderr_lower = (init_result.stderr or "").lower()
        if (
            "already initialized" in stderr_lower
            or "already been initialized" in stderr_lower
        ):
            logger.error(
                "Vault is already initialised; sunbeam did not return keys. "
                "Run 'sunbeam disable vault' then 'sunbeam enable vault', "
                "then run this test again to perform a fresh init and unseal."
            )
            return (False, None, None)
        logger.error("Failed to initialise Vault: %s", init_result.stderr)
        return (False, None, None)

    vault_data = json.loads(init_result.stdout or "{}")
    unseal_keys = vault_data.get("unseal_keys_b64") or vault_data.get("unseal_keys", [])
    unseal_key = unseal_keys[0] if unseal_keys else None
    root_token = vault_data.get("root_token")

    if unseal_key and root_token:
        logger.info("Vault initialised successfully; keys obtained")
        return (True, unseal_key, root_token)

    logger.error(
        "Vault init returned no keys (Vault may already be initialised). "
        "Run 'sunbeam disable vault' then 'sunbeam enable vault', then run "
        "this test again to perform a fresh init and unseal."
    )
    return (False, None, None)


def _unseal_vault(sunbeam, unseal_key: Optional[str]) -> None:
    """Unseal Vault per docs: KEY_THRESHOLD times for leader, then non-leaders.

    With 1 key and 1 threshold we run unseal twice (leader then non-leaders).
    """
    if not unseal_key:
        return

    sunbeam_cmd = getattr(sunbeam, "_sunbeam_cmd", "sunbeam")
    # First run unseals leader; second run unseals non-leader units (docs).
    for attempt in (1, 2):
        logger.info("Unseal run %d (leader then non-leaders)...", attempt)
        result = subprocess.run(
            [sunbeam_cmd, "vault", "unseal", "-"],
            input=unseal_key,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"sunbeam vault unseal failed (exit {result.returncode}): "
                f"{result.stderr or result.stdout}"
            )
        if attempt == 1 and "Rerun" in (result.stdout or ""):
            time.sleep(15)
    logger.info("Vault unsealing completed")


def _authorise_charm(sunbeam, root_token: Optional[str]) -> None:
    """Authorise Vault charm with root token (docs: authorize-charm -)."""
    if not root_token:
        return

    logger.info("Authorising Vault charm with root token...")
    sunbeam_cmd = getattr(sunbeam, "_sunbeam_cmd", "sunbeam")

    result = subprocess.run(
        [sunbeam_cmd, "vault", "authorize-charm", "-"],
        input=root_token,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"sunbeam vault authorize-charm failed (exit {result.returncode}): "
            f"{result.stderr or result.stdout}"
        )
    logger.info("Vault charm authorised")


def _disable_vault_and_wait(sunbeam, juju, timeout: int = 300) -> bool:
    """Disable Vault and wait until the vault app is gone. Returns True on success."""
    sunbeam_cmd = getattr(sunbeam, "_sunbeam_cmd", "sunbeam")
    logger.info("Disabling Vault to allow a fresh init (no keys from previous run)...")
    result = subprocess.run(
        [sunbeam_cmd, "disable", "vault"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        logger.error("sunbeam disable vault failed: %s", result.stderr or result.stdout)
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not juju.has_application("vault"):
            logger.info("Vault application removed")
            return True
        time.sleep(5)
    logger.error("Vault application did not disappear within %s seconds", timeout)
    return False


def ensure_vault_prerequisites(sunbeam, juju) -> bool:
    """Ensure Vault is ready (active).

    If Vault is already enabled (app exists), init/unseal/authorize were done
    when it was first enabled, so we only wait for the app to be ready.
    If Vault is not yet enabled, we enable it then run init, unseal, authorize,
    and wait for active. If init fails (Vault already initialised), we try
    disable/re-enable/retry once (only when no other feature depends on vault).
    """
    already_enabled = juju.has_application("vault")
    if not _ensure_vault_enabled(sunbeam, juju):
        return False

    if already_enabled:
        logger.info(
            "Vault was already enabled; skipping init/unseal/authorize and "
            "waiting for application ready."
        )
        juju.wait_for_application_ready("vault", timeout=360)
        return True

    success, unseal_key, root_token = _initialize_vault(sunbeam)
    if not success and juju.has_application("vault"):
        if not _disable_vault_and_wait(sunbeam, juju):
            return False
        if not _ensure_vault_enabled(sunbeam, juju):
            return False
        success, unseal_key, root_token = _initialize_vault(sunbeam)
    if not success:
        return False

    _unseal_vault(sunbeam, unseal_key)
    _authorise_charm(sunbeam, root_token)

    logger.info(
        "Waiting for Vault units to become active (docs: update-status-interval, e.g. 5 min)..."
    )
    juju.wait_for_application_ready("vault", timeout=360)
    return True


class VaultTest(BaseFeatureTest):
    """Test Vault feature enablement and readiness."""

    feature_name = "vault"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that Vault is fully set up and reachable via sunbeam."""
        if not ensure_vault_prerequisites(self.sunbeam, self.juju):
            raise AssertionError("Failed to set up Vault prerequisites")

        logger.info("Verifying Vault status via `sunbeam vault status`...")
        try:
            self.sunbeam.run(["vault", "status"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying Vault service: %s", exc)
            raise AssertionError(f"Vault service verification failed: {exc}") from exc

        logger.info("Vault service verified via `sunbeam vault status`")
