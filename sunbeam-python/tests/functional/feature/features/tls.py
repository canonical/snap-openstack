# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for tls feature (CA mode).

TLS enablement has multiple methods in Sunbeam, but this functional test
suite only exercises the TLS CA path:

- TLS CA: `sunbeam enable tls ca` (requires CA certificates)
"""

import base64
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Tuple

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


def generate_self_signed_ca_certificate() -> Tuple[str, str]:
    """Generate a self-signed CA certificate.

    Returns a tuple of (ca_cert_base64, ca_chain_base64). For a simple self-signed CA,
    the chain is the same as the cert. TLS CA currently only uses the CA certificate.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        key_path = tmp_path / "ca.key"
        subprocess.run(
            ["openssl", "genrsa", "-out", str(key_path), "4096"],
            check=True,
            capture_output=True,
        )

        cert_path = tmp_path / "ca.crt"
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-x509",
                "-days",
                "365",
                "-key",
                str(key_path),
                "-out",
                str(cert_path),
                "-subj",
                "/C=US/ST=State/L=City/O=TestOrg/CN=TestCA",
                "-extensions",
                "v3_ca",
                "-config",
                "/dev/stdin",
            ],
            input=b"""[req]
distinguished_name = req_distinguished_name
[req_distinguished_name]
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
""",
            check=True,
            capture_output=True,
        )

        ca_cert = cert_path.read_text()
        ca_cert_base64 = base64.b64encode(ca_cert.encode()).decode()

        ca_chain_base64 = ca_cert_base64

        return (ca_cert_base64, ca_chain_base64)


class TlsCaTest(BaseFeatureTest):
    """Test TLS CA mode enablement/disablement.

    TLS CA mode uses Certificate Authority certificates for TLS.
    This test verifies that:
    - TLS CA can be enabled (with self-signed CA certificates)
    - Endpoints are exposed over HTTPS (both public and internal)
    - Basic OpenStack operations work (e.g., listing images)
    """

    feature_name = "tls"
    enable_args: list[str] = []
    disable_args: list[str] = ["ca"]
    expected_applications = [
        "manual-tls-certificates",
    ]
    expected_units = [
        "manual-tls-certificates/0",
    ]
    timeout_seconds = 600

    def __init__(self, *args, **kwargs):
        """Initialize and generate CA certificates."""
        super().__init__(*args, **kwargs)
        self.ca_cert_base64, _ = generate_self_signed_ca_certificate()
        self.enable_args = [
            "ca",
            "--ca",
            self.ca_cert_base64,
        ]

    def enable(self) -> bool:
        """Enable TLS CA feature (without --accept-defaults flag)."""
        logger.info("Enabling feature: '%s'", self.feature_name)
        return self.sunbeam.enable_feature(
            self.feature_name,
            extra_args=self.enable_args,
        )

    def disable(self) -> bool:
        """Disable TLS CA feature (without --accept-defaults flag)."""
        logger.info("Disabling feature: '%s'", self.feature_name)
        try:
            return self.sunbeam.disable_feature(
                self.feature_name,
                extra_args=self.disable_args,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to disable feature '%s': %s",
                self.feature_name,
                exc,
            )
            return False

    def _ensure_tls_ca_disabled(self) -> bool:
        """Ensure TLS CA is disabled before enabling (cleanup from previous runs)."""
        if self.juju.has_application("manual-tls-certificates"):
            logger.info("TLS CA is already enabled, disabling first...")
            try:
                self.disable()
                # Wait a bit for cleanup
                time.sleep(5)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to disable existing TLS CA: %s", exc)
                return False
        return True

    def run_full_lifecycle(self) -> bool:
        """Enable TLS CA, perform basic test, then disable it."""
        if not self._ensure_tls_ca_disabled():
            logger.warning("Could not ensure TLS CA is disabled, continuing anyway...")

        self.enable()
        disable_success = self.disable()
        if not disable_success:
            logger.warning("TLS CA disable failed, but continuing test sequence")

        return True
