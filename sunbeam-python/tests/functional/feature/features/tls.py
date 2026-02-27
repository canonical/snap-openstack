# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for TLS feature (CA and Vault modes)."""

import base64
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Tuple

import yaml

from sunbeam.features.interface.utils import get_subject_from_csr
from cryptography import x509
from cryptography.x509.oid import NameOID
from .base import BaseFeatureTest
from .vault import ensure_vault_prerequisites

# TLS Vault root+intermediate CA: (ca_cert_b64, ca_chain_b64, inter_pem,
# inter_key_pem, vault_ca_conf, certindex, certserial)
VaultCaMaterial = Tuple[str, str, str, str, str, str, str]

logger = logging.getLogger(__name__)


def generate_self_signed_ca_certificate() -> Tuple[str, str]:
    """Generate a self-signed CA certificate.

    Returns a tuple of (ca_cert_base64, ca_chain_base64). For a simple self-signed CA,
    the chain is the same as the cert. TLS CA currently only uses the CA certificate.
    """
    cert_b64, chain_b64, _, _ = generate_self_signed_ca_certificate_with_key()
    return (cert_b64, chain_b64)


def generate_self_signed_ca_certificate_with_key() -> Tuple[str, str, str, str]:
    """Generate a self-signed CA certificate and private key.

    Returns (ca_cert_base64, ca_chain_base64, ca_cert_pem, ca_key_pem).
    Used when the test must sign CSRs (e.g. full TLS CA flow).
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

        ca_cert_pem = cert_path.read_text()
        ca_key_pem = key_path.read_text()
        ca_cert_base64 = base64.b64encode(ca_cert_pem.encode()).decode()
        ca_chain_base64 = ca_cert_base64

        return (ca_cert_base64, ca_chain_base64, ca_cert_pem, ca_key_pem)


def generate_root_and_intermediate_ca_for_vault() -> VaultCaMaterial:
    """Generate root CA and intermediate CA for TLS Vault.

    Follows the canonical doc:
    - Root CA: 8192-bit key, sha256, 3650 days, CA config with certindex/serial.
    - Intermediate CA: 8192-bit key, CSR signed by root via openssl ca -config
      ca.conf.
    - CA chain: intermediate then root (interca1.crt + rootca.crt).
    - Returns material so Vault CSR can be signed via openssl ca -config
      vault-ca.conf.

    Returns:
        (ca_cert_base64, ca_chain_base64, inter_cert_pem, inter_key_pem,
         vault_ca_conf_content, certindex_content, certserial_content).
        For enable use --ca=intermediate cert, --ca-chain=chain (inter+root).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # CA database and serial (per doc)
        (tmp_path / "certindex").touch()
        (tmp_path / "certserial").write_text("1000\n")
        (tmp_path / "crlnumber").write_text("1000\n")

        # Root CA config (per doc)
        ca_conf = """[ ca ]
default_ca = CA_default

[ CA_default ]
dir = .
database = certindex
new_certs_dir = .
certificate = rootca.crt
private_key = rootca.key
serial = certserial
default_days      = 375
default_crl_days  =  30
default_md        = sha256
policy = policy_anything
x509_extensions = v3_ca

[ v3_ca ]
basicConstraints = critical,CA:true

[ policy_anything ]
countryName = optional
stateOrProvinceName = optional
organizationName = optional
organizationalUnitName = optional
commonName = supplied
"""
        (tmp_path / "ca.conf").write_text(ca_conf)

        # Root CA key and cert (8192-bit, sha256, 3650 days)
        subprocess.run(
            ["openssl", "genrsa", "-out", "rootca.key", "8192"],
            check=True,
            capture_output=True,
            cwd=tmp_path,
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-sha256",
                "-new",
                "-x509",
                "-days",
                "3650",
                "-key",
                "rootca.key",
                "-out",
                "rootca.crt",
                "-subj",
                "/C=US/ST=State/L=City/O=TestOrg/CN=TestRootCA",
            ],
            check=True,
            capture_output=True,
            cwd=tmp_path,
        )

        # Intermediate CA key and CSR
        subprocess.run(
            ["openssl", "genrsa", "-out", "interca1.key", "8192"],
            check=True,
            capture_output=True,
            cwd=tmp_path,
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-sha256",
                "-new",
                "-key",
                "interca1.key",
                "-out",
                "interca1.csr",
                "-subj",
                "/C=US/ST=State/L=City/O=TestOrg/CN=TestInterCA",
            ],
            check=True,
            capture_output=True,
            cwd=tmp_path,
        )

        # Sign intermediate with root (openssl ca -batch -config ca.conf)
        subprocess.run(
            [
                "openssl",
                "ca",
                "-batch",
                "-config",
                "ca.conf",
                "-notext",
                "-in",
                "interca1.csr",
                "-out",
                "interca1.crt",
            ],
            check=True,
            capture_output=True,
            cwd=tmp_path,
        )

        # Chain: intermediate then root (per doc)
        inter_pem = (tmp_path / "interca1.crt").read_text()
        root_pem = (tmp_path / "rootca.crt").read_text()
        chain_pem = inter_pem + root_pem

        inter_key_pem = (tmp_path / "interca1.key").read_text()

        # vault-ca.conf uses intermediate as the signing CA (per doc)
        vault_ca_conf = """[ ca ]
default_ca = CA_default

[ CA_default ]
dir = .
database = certindex
new_certs_dir = .
certificate = interca1.crt
private_key = interca1.key
serial = certserial
default_days      = 375
default_crl_days  =  30
default_md        = sha256
policy = policy_anything
x509_extensions = v3_ca

[ v3_ca ]
basicConstraints = critical,CA:true

[ policy_anything ]
countryName = optional
stateOrProvinceName = optional
organizationName = optional
organizationalUnitName = optional
commonName = supplied

[alt_names]
DNS.1 = test.com
"""
        certindex_content = (tmp_path / "certindex").read_text()
        certserial_content = (tmp_path / "certserial").read_text()

        ca_cert_b64 = base64.b64encode(inter_pem.encode()).decode()
        ca_chain_b64 = base64.b64encode(chain_pem.encode()).decode()

        return (
            ca_cert_b64,
            ca_chain_b64,
            inter_pem,
            inter_key_pem,
            vault_ca_conf,
            certindex_content,
            certserial_content,
        )


class TlsCaTest(BaseFeatureTest):
    """Test TLS CA mode enablement/disablement (full flow).

    TLS CA mode uses Certificate Authority certificates for TLS.
    This test runs the complete flow:
    - Enable TLS CA with --ca and --endpoint public/internal/rgw
    - List outstanding CSRs (with retry/backoff)
    - Sign CSRs and update manifest
    - Push certs via sunbeam tls ca unit_certs -m manifest
    - Verify endpoints are HTTPS and basic OpenStack operations work.
    """

    feature_name = "tls"
    enable_args: list[str] = []
    disable_args: list[str] = ["ca"]
    expected_applications = [
        "manual-tls-certificates",
    ]
    timeout_seconds = 600

    def __init__(self, *args, **kwargs):
        """Initialise CA material (root+intermediate) for signing CSRs.

        For TLS CA we mirror the TLS Vault PKI model and the
        generate-a-ca-certificate.rst guide by generating a root CA and an
        intermediate CA, then using the intermediate as the issuing CA for
        Traefik CSRs. The intermediate certificate (base64) is passed via
        --ca, and its key is used to sign the outstanding CSRs; the chain
        (intermediate + root) is currently not surfaced but can be used if
        needed in future.
        """
        super().__init__(*args, **kwargs)
        (
            self.ca_cert_base64,
            self.ca_chain_base64,
            self._ca_cert_pem,
            self._ca_key_pem,
            _vault_ca_conf,
            _certindex,
            _certserial,
        ) = generate_root_and_intermediate_ca_for_vault()
        self.enable_args = [
            "ca",
            "--ca",
            self.ca_cert_base64,
            "--ca-chain",
            self.ca_chain_base64,
            "--endpoint",
            "public",
            "--endpoint",
            "internal",
            "--endpoint",
            "rgw",
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

    def _sign_ca_csrs_and_install(self) -> None:
        """List TLS CA CSRs (retry), sign with test CA, write manifest, unit_certs.

        Mirrors SQA enable_tls: list_outstanding_csrs -> sign -> manifest ->
        unit_certs. Certificate dict keyed by subject (X500 id from CSR).
        """
        sunbeam_cmd = getattr(self.sunbeam, "_sunbeam_cmd", "sunbeam")
        max_attempts = 10
        backoff_seconds = 15
        csrs_list: list = []
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "Listing outstanding TLS CA CSRs (attempt %d/%d)...",
                attempt,
                max_attempts,
            )
            result = subprocess.run(
                [
                    sunbeam_cmd,
                    "tls",
                    "ca",
                    "list_outstanding_csrs",
                    "--format",
                    "yaml",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            raw = yaml.safe_load(result.stdout or "") or []
            csrs_list = raw if isinstance(raw, list) else []
            if csrs_list:
                break
            if attempt < max_attempts:
                logger.info(
                    "No CSRs yet (Traefik may still be coming up); retrying in %ds...",
                    backoff_seconds,
                )
                time.sleep(backoff_seconds)

        if not csrs_list:
            logger.info(
                "No outstanding TLS CA CSRs after %d attempts; skipping unit_certs.",
                max_attempts,
            )
            return

        certificates: dict[str, dict[str, str]] = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            ca_cert_path = tmp_path / "ca.crt"
            ca_key_path = tmp_path / "ca.key"
            ca_cert_path.write_text(self._ca_cert_pem)
            ca_key_path.write_text(self._ca_key_pem)

            for record in csrs_list:
                if not isinstance(record, dict):
                    continue
                csr_pem = record.get("csr")
                if not csr_pem:
                    continue
                subject = get_subject_from_csr(str(csr_pem).strip())
                if not subject:
                    logger.warning("Could not get subject from CSR; skipping record")
                    continue
                csr_path = tmp_path / f"req_{subject[:8]}.csr"
                csr_path.write_text(str(csr_pem).strip() + "\n")
                cert_path = tmp_path / f"out_{subject[:8]}.crt"

                req = x509.load_pem_x509_csr(csr_path.read_bytes())
                cn = req.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
                san_txt = f"subjectAltName=DNS:{cn}\n"

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
                        "-extfile",
                        "/dev/stdin",
                    ],
                    input=san_txt,
                    text=True,
                    check=True,
                    capture_output=True,
                )
                cert_pem = cert_path.read_text()
                cert_b64 = base64.b64encode(cert_pem.encode()).decode()
                certificates[subject] = {"certificate": cert_b64}

        if not certificates:
            logger.warning("No certificates signed; skipping unit_certs")
            return

        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(Path.home()), suffix=".yaml", delete=False
        ) as f:
            manifest_data = {
                "features": {
                    "tls": {
                        "ca": {
                            "config": {"certificates": certificates},
                        },
                    },
                },
            }
            yaml.dump(manifest_data, f, default_flow_style=False, sort_keys=False)
            manifest_path = f.name

        try:
            logger.info(
                "Pushing signed certificates via 'sunbeam tls ca unit_certs -m %s'...",
                manifest_path,
            )
            subprocess.run(
                [sunbeam_cmd, "tls", "ca", "unit_certs", "-m", manifest_path],
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            Path(manifest_path).unlink(missing_ok=True)

    def validate_feature_behavior(self) -> None:
        """Check public/internal endpoints use HTTPS and image list works.

        This mirrors the verification logic used in the TLS Vault lifecycle
        tests and the upstream documentation:
        - Public endpoints must use HTTPS.
        - Internal endpoints (when present) must use HTTPS.
        - A basic OpenStack operation (image list) must succeed.
        """
        self._ensure_openstack_env()
        logger.info("Verifying public endpoints use HTTPS (TLS CA mode)...")
        result = subprocess.run(
            [
                "openstack",
                "endpoint",
                "list",
                "--interface",
                "public",
                "-c",
                "URL",
                "-f",
                "value",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        public_urls = [
            u.strip() for u in (result.stdout or "").splitlines() if u.strip()
        ]
        if not public_urls:
            raise AssertionError(
                "openstack endpoint list --interface public returned no URLs "
                "(TLS CA mode)"
            )
        public_https = [u for u in public_urls if u.startswith("https://")]
        if not public_https:
            raise AssertionError(
                "TLS CA feature appears inactive: no HTTPS endpoints found in "
                "public interface. Sample URLs: " + ", ".join(public_urls[:5])
            )
        logger.info("Found %d HTTPS public endpoints (TLS CA mode)", len(public_https))

        logger.info("Verifying internal endpoints use HTTPS (TLS CA mode)...")
        result = subprocess.run(
            [
                "openstack",
                "endpoint",
                "list",
                "--interface",
                "internal",
                "-c",
                "URL",
                "-f",
                "value",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        internal_urls = [
            u.strip() for u in (result.stdout or "").splitlines() if u.strip()
        ]
        if internal_urls:
            internal_https = [u for u in internal_urls if u.startswith("https://")]
            if not internal_https:
                raise AssertionError(
                    "TLS CA feature appears inactive: no HTTPS endpoints found in "
                    "internal interface. Sample URLs: " + ", ".join(internal_urls[:5])
                )
            logger.info(
                "Found %d HTTPS internal endpoints (TLS CA mode)",
                len(internal_https),
            )
        else:
            logger.warning(
                "No internal endpoints found; this may be normal for some deployments"
            )

        logger.info(
            "Verifying basic OpenStack operations work over TLS (TLS CA mode)..."
        )
        result = subprocess.run(
            ["openstack", "image", "list"],
            check=True,
            capture_output=True,
            text=True,
        )

    def run_full_lifecycle(self) -> bool:
        """Enable, sign CSRs, unit_certs, validate, optional disable."""
        logger.info("Starting full lifecycle test for TLS CA")
        if not self.enable():
            return False
        self._sign_ca_csrs_and_install()
        try:
            self.verify_validate_feature_behavior()
        except Exception:  # noqa: BLE001
            logger.exception("TLS CA validation failed")
            try:
                self.disable()
            except Exception:  # noqa: BLE001
                pass
            return False
        if not self.disable_after:
            logger.info("Leaving TLS CA enabled (disable_after is False)")
            return True
        self.disable()
        return True


class TlsVaultTest(BaseFeatureTest):
    """Test TLS Vault mode enablement/disablement.

    TLS Vault mode uses Vault for certificate management. CA material is generated
    per generate-a-ca-certificate.rst: root CA + intermediate CA, chain (intermediate
    then root), and the Vault CSR is signed with the intermediate via
    ``openssl ca -config vault-ca.conf``.

    Prerequisites (per docs):
    - Traefik hostnames must be configured.
    - Vault feature must be enabled.
    - Vault charm must be initialised, unsealed and authorised.

    run_full_lifecycle() does: enable with --ca/--ca-chain (intermediate + chain),
    list CSRs, sign Vault CSR with intermediate, unit_certs, then validate and disable.
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
            "--endpoint",
            "rgw",
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
        self._ensure_openstack_env()
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

    def _generate_vault_ca_material(self) -> VaultCaMaterial:
        """Generate root + intermediate CA for TLS Vault.

        See generate-a-ca-certificate.rst. Returns (ca_cert_base64, ca_chain_base64,
        inter_cert_pem, inter_key_pem,
                 vault_ca_conf, certindex_content, certserial_content).
        """
        return generate_root_and_intermediate_ca_for_vault()

    def _sign_vault_csrs_and_install(self) -> None:
        """Sign Vault CSR with intermediate CA, inject via sunbeam tls vault unit_certs.

        Per generate-a-ca-certificate.rst: sign Vault CSR with intermediate CA
        via: openssl ca -batch -config vault-ca.conf -notext -in vault.csr
        -out vault.crt
        """
        sunbeam_cmd = getattr(self.sunbeam, "_sunbeam_cmd", "sunbeam")

        max_attempts = 10
        backoff_seconds = 15
        records: list = []
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "Listing outstanding TLS Vault CSRs (attempt %d/%d)...",
                attempt,
                max_attempts,
            )
            result = subprocess.run(
                [
                    sunbeam_cmd,
                    "tls",
                    "vault",
                    "list_outstanding_csrs",
                    "--format",
                    "yaml",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            raw = yaml.safe_load(result.stdout or "") or []
            records = raw if isinstance(raw, list) else []
            if records:
                break
            if attempt < max_attempts:
                logger.info(
                    "No CSRs yet (Traefik may still be coming up); retrying in %ds...",
                    backoff_seconds,
                )
                time.sleep(backoff_seconds)

        if not records:
            logger.info(
                "No outstanding TLS Vault CSRs found after %d attempts; "
                "skipping unit_certs step.",
                max_attempts,
            )
            return

        first = records[0] if isinstance(records[0], dict) else {}
        csr_pem = first.get("csr")
        unit_name = first.get("unit_name") or first.get("app_name") or "vault/0"
        if not csr_pem:
            logger.warning(
                "First TLS Vault CSR record had no 'csr'; skipping unit_certs"
            )
            return
        if len(records) > 1:
            logger.warning(
                "Multiple TLS Vault CSRs found; signing the first only for this test."
            )
        logger.info(
            "Signing Vault CSR for unit %s with intermediate CA (openssl ca -config vault-ca.conf)",
            unit_name,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Recreate CA dir state for intermediate to sign the Vault CSR.
            (tmp_path / "vault-ca.conf").write_text(self._vault_ca_conf)
            (tmp_path / "interca1.crt").write_text(self._vault_ca_cert_pem)
            (tmp_path / "interca1.key").write_text(self._vault_ca_key_pem)
            (tmp_path / "certindex").write_text(self._vault_certindex)
            (tmp_path / "certserial").write_text(self._vault_certserial)
            (tmp_path / "crlnumber").write_text("1000\n")
            (tmp_path / "vault.csr").write_text(str(csr_pem).strip() + "\n")

            subprocess.run(
                [
                    "openssl",
                    "ca",
                    "-batch",
                    "-config",
                    "vault-ca.conf",
                    "-notext",
                    "-in",
                    "vault.csr",
                    "-out",
                    "vault.crt",
                ],
                check=True,
                capture_output=True,
                cwd=tmp_path,
            )

            vault_cert_pem = (tmp_path / "vault.crt").read_text()

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

        # Generate root + intermediate CA.
        (
            self.ca_cert_base64,
            self.ca_chain_base64,
            self._vault_ca_cert_pem,
            self._vault_ca_key_pem,
            self._vault_ca_conf,
            self._vault_certindex,
            self._vault_certserial,
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
            "--endpoint",
            "rgw",
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
