# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for TLS feature (CA and Vault modes)."""

import base64
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple

import yaml

from .base import BaseFeatureTest
from .vault import ensure_vault_prerequisites

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


class TlsVaultTest(BaseFeatureTest):
    """Test TLS Vault mode enablement/disablement.

    TLS Vault mode uses Vault for certificate management.
    Prerequisites (per docs):
    - Traefik hostnames must be configured.
    - Vault feature must be enabled.
    - Vault charm must be initialised, unsealed and authorised.

    This test focuses on the enable/disable flow and a couple of
    high-level functional checks:
    - TLS Vault can be enabled (after Vault is ready).
    - Public and internal endpoints use HTTPS.
    - A basic OpenStack operation succeeds (image list).
    """

    feature_name = "tls"
    enable_args: list[str] = []
    # Per docs, disable is done via `sunbeam disable tls vault`.
    disable_args: list[str] = ["vault"]
    expected_applications: list[str] = []
    timeout_seconds = 600

    def __init__(self, *args, **kwargs):
        """Initialise and generate CA material for TLS Vault enablement.

        Docs require providing a CA certificate and CA chain when enabling
        TLS Vault, so we mirror the TLS CA test by generating a simple
        self-signed CA on the fly and passing it via --ca / --ca-chain.
        """
        super().__init__(*args, **kwargs)
        self.ca_cert_base64, self.ca_chain_base64 = (
            generate_self_signed_ca_certificate()
        )
        self.enable_args = [
            "vault",
            "--ca",
            self.ca_cert_base64,
            "--ca-chain",
            self.ca_chain_base64,
            "--endpoint",
            "public",
            "--endpoint",
            "internal",
        ]

    def verify_enabled(self) -> None:
        """Optionally verify Vault-related resources.

        Currently kept minimal; no specific applications are asserted beyond
        successful enablement and behavioral checks.
        """
        if not self.expected_applications:
            return

        for app in self.expected_applications:
            self.juju.wait_for_application(app, timeout=self.timeout_seconds)

    def validate_feature_behavior(self) -> None:
        """High-level TLS Vault behavior checks.

        - Public and internal endpoints use HTTPS.
        - A basic OpenStack CLI operation (image list) works.
        """
        logger.info("Verifying public endpoints use HTTPS (TLS Vault mode)...")
        cmd = [
            "openstack",
            "endpoint",
            "list",
            "--interface",
            "public",
            "-c",
            "URL",
            "-f",
            "value",
        ]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        public_urls = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]

        if not public_urls:
            raise AssertionError(
                "openstack endpoint list --interface public returned no URLs"
            )

        public_https_urls = [u for u in public_urls if u.startswith("https://")]
        if not public_https_urls:
            raise AssertionError(
                "TLS Vault feature appears inactive: no HTTPS endpoints found in "
                "public interface. Sample URLs: " + ", ".join(public_urls[:5])
            )

        logger.info(
            "Found %d HTTPS public endpoints (TLS Vault mode)",
            len(public_https_urls),
        )

        logger.info("Verifying internal endpoints use HTTPS (TLS Vault mode)...")
        cmd = [
            "openstack",
            "endpoint",
            "list",
            "--interface",
            "internal",
            "-c",
            "URL",
            "-f",
            "value",
        ]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        internal_urls = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]

        if internal_urls:
            internal_https_urls = [u for u in internal_urls if u.startswith("https://")]
            if not internal_https_urls:
                raise AssertionError(
                    "TLS Vault feature appears inactive: no HTTPS endpoints found in "
                    "internal interface. Sample URLs: " + ", ".join(internal_urls[:5])
                )
            logger.info(
                "Found %d HTTPS internal endpoints (TLS Vault mode)",
                len(internal_https_urls),
            )
        else:
            logger.warning(
                "No internal endpoints found; this may be normal for some deployments"
            )

        logger.info(
            "Verifying basic OpenStack operations work over TLS (TLS Vault mode)..."
        )
        cmd = ["openstack", "image", "list"]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(
                "Basic OpenStack operation failed after TLS Vault enablement: "
                f"openstack image list returned error: {result.stderr}"
            )
        logger.info("Basic OpenStack operations verified (TLS Vault mode)")

    def _generate_vault_ca_material(self) -> Tuple[str, str, str, str]:
        """Generate CA key and certificate for TLS Vault workflow.

        Returns (ca_cert_base64, ca_chain_base64, ca_cert_pem, ca_key_pem).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            key_path = tmp_path / "vault-ca.key"
            cert_path = tmp_path / "vault-ca.crt"

            subprocess.run(
                ["openssl", "genrsa", "-out", str(key_path), "4096"],
                check=True,
                capture_output=True,
            )
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
                    "/C=US/ST=State/L=City/O=TestOrg/CN=TestVaultCA",
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

            ca_cert_pem = cert_path.read_text()
            ca_key_pem = key_path.read_text()
            ca_cert_base64 = base64.b64encode(ca_cert_pem.encode()).decode()
            ca_chain_base64 = ca_cert_base64

        return ca_cert_base64, ca_chain_base64, ca_cert_pem, ca_key_pem

    def _sign_vault_csrs_and_install(self) -> None:
        """Automate CSR signing and certificate injection for TLS Vault.

        This follows the docs flow:
        - List outstanding Vault CSRs.
        - Act as the external CA and sign them with our test CA.
        - Provide the signed certificate to ``sunbeam tls vault unit_certs``.
        """
        sunbeam_cmd = getattr(self.sunbeam, "_sunbeam_cmd", "sunbeam")

        logger.info("Listing outstanding TLS Vault CSRs...")
        result = subprocess.run(
            [sunbeam_cmd, "tls", "vault", "list_outstanding_csrs", "--format", "yaml"],
            check=True,
            capture_output=True,
            text=True,
        )

        data = yaml.safe_load(result.stdout or "") or {}
        if not isinstance(data, dict) or not data:
            logger.info(
                "No outstanding TLS Vault CSRs found; skipping unit_certs step."
            )
            return

        # For now, we handle the common case of a single Vault unit.
        if len(data) > 1:
            logger.warning(
                "Multiple Vault CSRs found; signing the first one only for this test."
            )

        unit_name, csr_pem = next(iter(data.items()))
        logger.info("Signing CSR for Vault unit %s", unit_name)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ca_cert_path = tmp_path / "ca.crt"
            ca_key_path = tmp_path / "ca.key"
            csr_path = tmp_path / "vault.csr"
            cert_path = tmp_path / "vault.crt"

            ca_cert_path.write_text(self._vault_ca_cert_pem)
            ca_key_path.write_text(self._vault_ca_key_pem)
            csr_path.write_text(str(csr_pem).strip() + "\n")

            subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-in",
                    str(csr_path),
                    "-CA",
                    str(ca_cert_path),
                    "-CAkey",
                    str(ca_key_path),
                    "-CAcreateserial",
                    "-out",
                    str(cert_path),
                    "-days",
                    "365",
                ],
                check=True,
                capture_output=True,
            )

            vault_cert_pem = cert_path.read_text()

        vault_cert_b64 = base64.b64encode(vault_cert_pem.encode()).decode()

        logger.info(
            "Injecting signed Vault certificate via 'sunbeam tls vault unit_certs'..."
        )
        subprocess.run(
            [sunbeam_cmd, "tls", "vault", "unit_certs"],
            input=f"{vault_cert_b64}\n",
            check=True,
            capture_output=True,
            text=True,
        )

    def run_full_lifecycle(self) -> bool:
        """Enable TLS Vault, run minimal checks, then disable it.

        Focuses on correct, deterministic flow:
        - Ensure Vault prerequisites are met.
        - Enable TLS Vault.
        - Run minimal behaviour checks.
        - Disable TLS Vault (failure logged but not fatal).
        """
        if not ensure_vault_prerequisites(self.sunbeam, self.juju):
            logger.error("Failed to set up Vault prerequisites for TLS Vault")
            return False

        # Generate dedicated CA material for TLS Vault and wire enable args.
        (
            self.ca_cert_base64,
            self.ca_chain_base64,
            self._vault_ca_cert_pem,
            self._vault_ca_key_pem,
        ) = self._generate_vault_ca_material()
        self.enable_args = [
            "vault",
            "--ca",
            self.ca_cert_base64,
            "--ca-chain",
            self.ca_chain_base64,
            "--endpoint",
            "public",
            "--endpoint",
            "internal",
        ]

        self.enable()

        # Automate the external CA flow: list CSRs, sign with our CA, and
        # provide the signed certificate back to Vault.
        self._sign_vault_csrs_and_install()

        self.verify_enabled()
        self.validate_feature_behavior()

        disable_success = self.disable()
        if not disable_success:
            logger.warning("TLS Vault disable failed, but continuing test sequence")

        return True
