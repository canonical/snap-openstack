# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import base64
import datetime
from unittest.mock import patch

import click
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from sunbeam.features.interface.utils import (
    decode_base64_as_string,
    encode_base64_as_string,
    generate_ca_chain,
    validate_ca_certificate,
    validate_ca_chain,
)


def _make_cert(
    name: str,
    key: rsa.RSAPrivateKey,
    issuer_key: rsa.RSAPrivateKey | None = None,
    issuer_cert: x509.Certificate | None = None,
    ca: bool | None = None,
) -> x509.Certificate:
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer_key = issuer_key or key
    if ca is None:
        ca = issuer_cert is None
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_cert.subject if issuer_cert else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
        )
        .add_extension(
            x509.BasicConstraints(ca=ca, path_length=None),
            critical=True,
        )
        .sign(issuer_key, hashes.SHA256())
    )


def test_generate_ca_chain():
    cert1 = "CERT1"
    cert2 = "CERT2"
    cert3 = "CERT3"

    ca_chain = generate_ca_chain(
        encode_base64_as_string(cert1),
        encode_base64_as_string(cert2),
        encode_base64_as_string(cert3),
    )
    ca_chain_decoded = decode_base64_as_string(ca_chain)
    expected_chain = cert1 + "\n" + cert2 + "\n" + cert3
    assert ca_chain_decoded == expected_chain


def test_validate_ca_certificate_normalizes_crlf():
    """validate_ca_certificate strips CRLF and returns clean base64."""
    cert_crlf = b"-----BEGIN CERTIFICATE-----\r\nDATA\r\n-----END CERTIFICATE-----\r\n"
    cert_lf = b"-----BEGIN CERTIFICATE-----\nDATA\n-----END CERTIFICATE-----\n"
    encoded = base64.b64encode(cert_crlf).decode()

    with patch("sunbeam.features.interface.utils.x509.load_pem_x509_certificate"):
        result = validate_ca_certificate(None, None, encoded)

    assert base64.b64decode(result) == cert_lf


def test_validate_ca_chain_normalizes_crlf():
    """validate_ca_chain strips CRLF and returns clean base64."""
    chain_crlf = (
        b"-----BEGIN CERTIFICATE-----\r\nISSUINGCA\r\n-----END CERTIFICATE-----\r\n"
        b"-----BEGIN CERTIFICATE-----\r\nROOTCA\r\n-----END CERTIFICATE-----\r\n"
    )
    chain_lf = (
        b"-----BEGIN CERTIFICATE-----\nISSUINGCA\n-----END CERTIFICATE-----\n"
        b"-----BEGIN CERTIFICATE-----\nROOTCA\n-----END CERTIFICATE-----\n"
    )
    encoded = base64.b64encode(chain_crlf).decode()

    with (
        patch("sunbeam.features.interface.utils.x509.load_pem_x509_certificate"),
        patch("sunbeam.features.interface.utils.is_ca_certificate", return_value=True),
    ):
        result = validate_ca_chain(None, None, encoded)

    assert base64.b64decode(result) == chain_lf


def test_validate_ca_chain_rejects_leaf_first_chain() -> None:
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_cert = _make_cert("root-ca", root_key)
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = _make_cert(
        "leaf",
        leaf_key,
        issuer_key=root_key,
        issuer_cert=root_cert,
    )
    chain = leaf_cert.public_bytes(serialization.Encoding.PEM) + root_cert.public_bytes(
        serialization.Encoding.PEM
    )

    with pytest.raises(
        click.BadParameter,
        match="The ca-chain contains a non-CA certificate.",
    ):
        validate_ca_chain(None, None, base64.b64encode(chain).decode())


def test_validate_ca_chain_rejects_non_ca_middle_certificate() -> None:
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_cert = _make_cert("root-ca", root_key, ca=True)

    # "leaf" here is first in the chain but is itself CA-flagged, so it
    # passes the individual CA check. The middle certificate is the one
    # that is not CA-flagged and should trigger the rejection.
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = _make_cert(
        "leaf-ca",
        leaf_key,
        issuer_key=root_key,
        issuer_cert=root_cert,
        ca=True,
    )

    mid_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    mid_cert = _make_cert(
        "mid-non-ca",
        mid_key,
        issuer_key=leaf_key,
        issuer_cert=leaf_cert,
        ca=False,
    )

    chain = (
        leaf_cert.public_bytes(serialization.Encoding.PEM)
        + mid_cert.public_bytes(serialization.Encoding.PEM)
        + root_cert.public_bytes(serialization.Encoding.PEM)
    )

    with pytest.raises(
        click.BadParameter,
        match="The ca-chain contains a non-CA certificate.",
    ):
        validate_ca_chain(None, None, base64.b64encode(chain).decode())
