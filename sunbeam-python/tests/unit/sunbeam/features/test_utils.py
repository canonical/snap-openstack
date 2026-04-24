# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import base64
from unittest.mock import patch

from sunbeam.features.interface.utils import (
    decode_base64_as_string,
    encode_base64_as_string,
    generate_ca_chain,
    validate_ca_certificate,
    validate_ca_chain,
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

    with patch("sunbeam.features.interface.utils.x509.load_pem_x509_certificate"):
        result = validate_ca_chain(None, None, encoded)

    assert base64.b64decode(result) == chain_lf
